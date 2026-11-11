package main

import (
	"math"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ipState tracks per-IP failure history and any active lockout.
type ipState struct {
	// failures holds the timestamp of each recent *failed* auth attempt.
	// Successful auths do not consume the budget. Entries older than the
	// window are pruned on every interaction, keeping memory bounded.
	failures []time.Time

	// lockoutUntil is zero when the IP is not locked out. Once maxAttempts
	// failures accumulate within the window, it is set to now+lockout.
	lockoutUntil time.Time
}

// RateLimiter is a per-IP sliding-window failure-counting rate limiter with
// lockout support.
//
// Design:
//   - **Only failures count.** Legitimate users making lots of successful
//     requests (e.g., Caddy's forward_auth, which hits /auth/verify on every
//     protected-route request) do not consume budget. Only actual 401 returns
//     from the auth handler count toward the lockout threshold.
//   - Sliding window: avoids the burst-at-bucket-boundary problem where an
//     attacker can make 2×maxAttempts in a short span by timing requests to
//     straddle two fixed windows.
//   - In-memory state: ldap-proxy is single-process, single-replica. No need
//     for distributed state.
//   - Lockout distinct from window: once maxAttempts failures accumulate, the
//     IP is blocked for the longer lockout duration — gives defenders time to
//     react and penalises automated scanners.
type RateLimiter struct {
	window      time.Duration
	lockout     time.Duration
	maxAttempts int
	clock       func() time.Time

	mu    sync.Mutex
	state map[string]*ipState
}

// NewRateLimiter creates a limiter with the given window, lockout, max failures,
// and clock function.
//
//   - window:      period over which failures are counted (e.g. 5 minutes).
//   - lockout:     how long an IP stays blocked after exceeding maxAttempts.
//   - maxAttempts: number of failures permitted within a window before blocking.
//   - clock:       time source; pass time.Now for production, a fake for tests.
func NewRateLimiter(window, lockout time.Duration, maxAttempts int, clock func() time.Time) *RateLimiter {
	return &RateLimiter{
		window:      window,
		lockout:     lockout,
		maxAttempts: maxAttempts,
		clock:       clock,
		state:       make(map[string]*ipState),
	}
}

// CheckLockout reports whether an IP is currently locked out. It does NOT
// record anything — use RecordFailure to register a failed auth attempt.
//
// Returns:
//   - allowed=true, retryAfter=0 when the IP is not locked out.
//   - allowed=false, retryAfter>0 when the IP is locked out; retryAfter is
//     the remaining lockout duration.
//
// Safe for concurrent use.
func (rl *RateLimiter) CheckLockout(ip string) (allowed bool, retryAfter time.Duration) {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	st, ok := rl.state[ip]
	if !ok {
		return true, 0
	}

	now := rl.clock()
	if now.Before(st.lockoutUntil) {
		return false, st.lockoutUntil.Sub(now)
	}
	return true, 0
}

// RecordFailure registers a failed authentication attempt from ip. If the
// failure count within the sliding window reaches maxAttempts, the IP is
// locked out for the configured lockout duration.
//
// Safe for concurrent use.
func (rl *RateLimiter) RecordFailure(ip string) {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	now := rl.clock()

	st, ok := rl.state[ip]
	if !ok {
		if len(rl.state) >= pruneAbove {
			rl.prune(now)
		}
		st = &ipState{}
		rl.state[ip] = st
	}

	// If already locked out, don't extend — the existing lockout stands.
	if now.Before(st.lockoutUntil) {
		return
	}

	// Prune failures that have aged out of the sliding window.
	cutoff := now.Add(-rl.window)
	fresh := st.failures[:0]
	for _, ts := range st.failures {
		if ts.After(cutoff) {
			fresh = append(fresh, ts)
		}
	}
	st.failures = fresh

	// Record the failure.
	st.failures = append(st.failures, now)

	// If we've hit the threshold, apply lockout. We set lockout on the
	// failure that fills the last slot so subsequent CheckLockout calls
	// (even after the window has rolled past the recorded failures) still
	// see the lockout.
	if len(st.failures) >= rl.maxAttempts {
		st.lockoutUntil = now.Add(rl.lockout)
	}
}

// pruneAbove is the number of tracked keys at which RecordFailure first drops
// the ones with nothing left to remember. Keys come from the internet (source
// addresses, and account names on the LDAP listener), so without this the map
// only grows.
const pruneAbove = 4096

// prune drops keys with no lockout in force and no failure inside the window.
// The caller holds rl.mu.
func (rl *RateLimiter) prune(now time.Time) {
	cutoff := now.Add(-rl.window)
	for key, st := range rl.state {
		if now.Before(st.lockoutUntil) {
			continue
		}
		if n := len(st.failures); n > 0 && st.failures[n-1].After(cutoff) {
			continue
		}
		delete(rl.state, key)
	}
}

// clientIP extracts the rate-limit key from a request.
//
// If X-Forwarded-For is present and its first entry is a valid IP, that IP is
// used. Caddy replaces the header with the address it saw, because its
// forward_auth blocks trust no proxies; a client-sent value never arrives here
// through Caddy. Otherwise the host portion of r.RemoteAddr is used.
func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		first := strings.TrimSpace(strings.SplitN(xff, ",", 2)[0])
		if net.ParseIP(first) != nil {
			return first
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

// Middleware wraps an http.Handler with lockout enforcement.
//
// The middleware ONLY checks whether the source IP is currently locked out —
// it does not record failures. The wrapped handler is responsible for calling
// RecordFailure when an auth attempt fails (e.g., 401 response).
//
// On lockout, returns 429 with Retry-After set to seconds until the next
// permitted attempt.
func (rl *RateLimiter) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip := clientIP(r)
		allowed, retryAfter := rl.CheckLockout(ip)
		if !allowed {
			secs := int(math.Ceil(retryAfter.Seconds()))
			w.Header().Set("Retry-After", strconv.Itoa(secs))
			http.Error(w, "rate limit exceeded", http.StatusTooManyRequests)
			return
		}
		next.ServeHTTP(w, r)
	})
}
