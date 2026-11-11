package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// fakeClock returns a clock function backed by a *time.Time pointer so tests
// can advance time deterministically without sleeping.
func fakeClock(t *time.Time) func() time.Time {
	return func() time.Time { return *t }
}

func advance(t *time.Time, d time.Duration) {
	*t = t.Add(d)
}

// ---------------------------------------------------------------------------
// RateLimiter core (CheckLockout + RecordFailure)
// ---------------------------------------------------------------------------

func TestRateLimiter_FreshIP_NotLockedOut(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	allowed, retryAfter := rl.CheckLockout("10.0.0.1")
	if !allowed {
		t.Fatal("fresh IP: expected allowed, got blocked")
	}
	if retryAfter != 0 {
		t.Fatalf("fresh IP: expected retryAfter=0, got %v", retryAfter)
	}
}

func TestRateLimiter_FailuresUnderLimit_NotLockedOut(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Record 2 failures — one short of the limit.
	rl.RecordFailure("10.0.0.2")
	rl.RecordFailure("10.0.0.2")

	allowed, _ := rl.CheckLockout("10.0.0.2")
	if !allowed {
		t.Fatal("2 of 3 failures: expected still allowed, got blocked")
	}
}

func TestRateLimiter_FailuresAtLimit_LockoutApplies(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	lockout := 5 * time.Minute
	rl := NewRateLimiter(1*time.Minute, lockout, 3, fakeClock(&now))

	// Fill the budget with failures.
	for i := 0; i < 3; i++ {
		rl.RecordFailure("10.0.0.3")
	}

	allowed, retryAfter := rl.CheckLockout("10.0.0.3")
	if allowed {
		t.Fatal("after maxAttempts failures: expected blocked, got allowed")
	}
	if retryAfter <= 0 || retryAfter > lockout {
		t.Fatalf("unexpected retryAfter: %v (lockout=%v)", retryAfter, lockout)
	}
}

func TestRateLimiter_SuccessesDontConsumeBudget(t *testing.T) {
	// Regression test for the DD-037 fix: a legitimate user hitting
	// /auth/verify 1000 times should not get locked out because only
	// failures count toward the threshold. CheckLockout is a pure read.
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	for i := 0; i < 1000; i++ {
		allowed, _ := rl.CheckLockout("10.0.0.4")
		if !allowed {
			t.Fatalf("after %d CheckLockout calls: expected allowed, got blocked", i+1)
		}
	}
}

func TestRateLimiter_WindowRollover_ClearsFailures(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	window := 1 * time.Minute
	rl := NewRateLimiter(window, 5*time.Minute, 3, fakeClock(&now))

	// Two failures — one short of lockout.
	rl.RecordFailure("10.0.0.5")
	rl.RecordFailure("10.0.0.5")

	// Age those out of the window.
	advance(&now, window+1*time.Second)

	// One more failure should NOT trigger lockout (pruned earlier ones).
	rl.RecordFailure("10.0.0.5")
	allowed, _ := rl.CheckLockout("10.0.0.5")
	if !allowed {
		t.Fatal("after window rollover + 1 new failure: expected allowed")
	}
}

func TestRateLimiter_LockoutExpires_Unblocks(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	lockout := 5 * time.Minute
	rl := NewRateLimiter(1*time.Minute, lockout, 3, fakeClock(&now))

	// Trigger lockout.
	for i := 0; i < 3; i++ {
		rl.RecordFailure("10.0.0.6")
	}

	advance(&now, lockout+1*time.Second)

	allowed, retryAfter := rl.CheckLockout("10.0.0.6")
	if !allowed {
		t.Fatal("after lockout expiry: expected allowed")
	}
	if retryAfter != 0 {
		t.Fatalf("after lockout expiry: expected retryAfter=0, got %v", retryAfter)
	}
}

func TestRateLimiter_PerIPIsolation(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Lock out IP A.
	for i := 0; i < 3; i++ {
		rl.RecordFailure("10.0.0.7")
	}

	// IP B should be unaffected.
	allowed, _ := rl.CheckLockout("10.0.0.8")
	if !allowed {
		t.Fatal("per-IP isolation: IP B should be allowed when IP A is locked out")
	}
}

func TestRateLimiter_AlreadyLockedOut_RecordFailureDoesntExtend(t *testing.T) {
	// An already-locked-out IP that keeps retrying shouldn't have its lockout
	// extended further — that would effectively create a permanent DoS with
	// steady brute-force traffic. The initial lockout duration is the penalty.
	now := time.Unix(1_000_000, 0)
	lockout := 5 * time.Minute
	rl := NewRateLimiter(1*time.Minute, lockout, 3, fakeClock(&now))

	// Trigger lockout at t=0.
	for i := 0; i < 3; i++ {
		rl.RecordFailure("10.0.0.9")
	}

	// Attacker keeps hammering for 3 minutes.
	for i := 0; i < 100; i++ {
		advance(&now, 2*time.Second)
		rl.RecordFailure("10.0.0.9")
	}

	// Advance past the ORIGINAL 5-minute lockout (with some slack for the
	// 3 minutes we already advanced).
	advance(&now, 3*time.Minute)

	allowed, _ := rl.CheckLockout("10.0.0.9")
	if !allowed {
		t.Fatal("after original lockout elapsed: lockout should not have been extended by retries")
	}
}

// ---------------------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------------------

// dummyHandler returns 200 OK and records how many times it was called.
type dummyHandler struct{ calls int }

func (h *dummyHandler) ServeHTTP(w http.ResponseWriter, _ *http.Request) {
	h.calls++
	w.WriteHeader(http.StatusOK)
}

func TestMiddleware_SuccessfulRequests_DontLockOut(t *testing.T) {
	// Regression test for DD-037. The middleware only checks lockout state;
	// it does NOT record anything. A handler that always returns 200 should
	// therefore never get its caller rate-limited.
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	inner := &dummyHandler{}
	handler := rl.Middleware(inner)

	// 100 successful requests from the same IP.
	for i := 0; i < 100; i++ {
		req := httptest.NewRequest("GET", "/auth/verify", nil)
		req.RemoteAddr = "10.0.0.10:12345"
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		if rr.Code != http.StatusOK {
			t.Fatalf("request %d: expected 200, got %d (regression — successes shouldn't lock out)", i+1, rr.Code)
		}
	}
	if inner.calls != 100 {
		t.Fatalf("expected 100 handler calls, got %d", inner.calls)
	}
}

func TestMiddleware_LockedOutIP_Returns429(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Manually lock out an IP via direct RecordFailure calls.
	for i := 0; i < 3; i++ {
		rl.RecordFailure("10.0.0.11")
	}

	inner := &dummyHandler{}
	handler := rl.Middleware(inner)

	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "10.0.0.11:54321"
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("locked-out IP: expected 429, got %d", rr.Code)
	}
	if rr.Header().Get("Retry-After") == "" {
		t.Fatal("expected Retry-After header")
	}
	if inner.calls != 0 {
		t.Fatalf("locked-out request should not reach handler, but handler was called %d times", inner.calls)
	}
}

func TestMiddleware_XForwardedFor_UsedForLockoutKey(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Lock out via XFF "10.0.0.20".
	for i := 0; i < 3; i++ {
		rl.RecordFailure("10.0.0.20")
	}

	handler := rl.Middleware(&dummyHandler{})

	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "127.0.0.1:1111"
	req.Header.Set("X-Forwarded-For", "10.0.0.20")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 keyed on XFF, got %d", rr.Code)
	}
}

func TestMiddleware_MultipleIPsInXForwardedFor_UsesFirst(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Lock out the first IP in a multi-value XFF.
	for i := 0; i < 3; i++ {
		rl.RecordFailure("1.2.3.4")
	}

	handler := rl.Middleware(&dummyHandler{})

	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "127.0.0.1:3333"
	req.Header.Set("X-Forwarded-For", "1.2.3.4, 5.6.7.8")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 for first XFF IP, got %d", rr.Code)
	}

	// Only the forwarding proxy (5.6.7.8) should not trigger lockout.
	inner := &dummyHandler{}
	handler2 := rl.Middleware(inner)
	req2 := httptest.NewRequest("GET", "/auth/verify", nil)
	req2.RemoteAddr = "127.0.0.1:5555"
	req2.Header.Set("X-Forwarded-For", "5.6.7.8")
	rr2 := httptest.NewRecorder()
	handler2.ServeHTTP(rr2, req2)

	if rr2.Code != http.StatusOK {
		t.Fatalf("5.6.7.8 should not be rate-limited, got %d", rr2.Code)
	}
}

func TestMiddleware_NoXForwardedFor_FallsBackToRemoteAddr(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	// Lock out via the host part of a RemoteAddr.
	for i := 0; i < 3; i++ {
		rl.RecordFailure("192.168.1.1")
	}

	handler := rl.Middleware(&dummyHandler{})

	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "192.168.1.1:7777" // same host, different port
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 from RemoteAddr fallback, got %d", rr.Code)
	}
}

func TestMiddleware_MalformedXForwardedFor_FallsBackToRemoteAddr(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(1*time.Minute, 5*time.Minute, 3, fakeClock(&now))

	for i := 0; i < 3; i++ {
		rl.RecordFailure("172.16.0.1")
	}

	handler := rl.Middleware(&dummyHandler{})

	req := httptest.NewRequest("GET", "/auth/verify", nil)
	req.RemoteAddr = "172.16.0.1:9999"
	req.Header.Set("X-Forwarded-For", "!!!not-an-ip!!!")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 after malformed XFF falls back to RemoteAddr, got %d", rr.Code)
	}
}

func TestKeysWithNothingToRememberArePruned(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	rl := NewRateLimiter(time.Minute, 5*time.Minute, 2, fakeClock(&now))

	rl.RecordFailure("locked")
	rl.RecordFailure("locked")
	for i := range pruneAbove {
		rl.RecordFailure(fmt.Sprintf("stale-%d", i))
	}
	now = now.Add(2 * time.Minute)
	rl.RecordFailure("recent")
	rl.RecordFailure("trigger")

	if len(rl.state) > 4 {
		t.Errorf("%d keys kept, want the stale ones dropped", len(rl.state))
	}
	if allowed, _ := rl.CheckLockout("locked"); allowed {
		t.Error("pruning dropped a lockout that was still in force")
	}
	if _, ok := rl.state["recent"]; !ok {
		t.Error("pruning dropped a key with a failure inside the window")
	}
}
