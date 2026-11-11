package main

import (
	"encoding/base64"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// newVerifyHandler returns an AuthHandler whose LLDAP bind is a stub that knows
// one user, so these tests need no LLDAP, plus that user's password.
func newVerifyHandler(t *testing.T, username string) (*AuthHandler, string) {
	t.Helper()
	const password = "correct horse battery staple"
	proxy := NewLDAPProxy(tempDB(t), "127.0.0.1:1", "dc=takldap", "", "")
	proxy.lldapBind = func(bindDN, got string) (bool, error) {
		return bindDN == "uid="+username+",ou=people,dc=takldap" && got == password, nil
	}
	return NewAuthHandler(proxy, nil), password
}

func verify(h *AuthHandler, target, username, password string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("GET", target, nil)
	credential := base64.StdEncoding.EncodeToString([]byte(username + ":" + password))
	req.Header.Set("Authorization", "Basic "+credential)
	rr := httptest.NewRecorder()
	h.HandleVerify(rr, req)
	return rr
}

func TestVerifyReturnsIdentityAndGroups(t *testing.T) {
	h, token := newVerifyHandler(t, "alice")
	h.groupLookup = func(string) ([]string, error) { return []string{"a", "b"}, nil }

	rr := verify(h, "/auth/verify", "alice", token)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
	if got := rr.Header().Get("Remote-User"); got != "alice" {
		t.Errorf("Remote-User = %q", got)
	}
	if got := rr.Header().Get("Remote-Groups"); got != "a,b" {
		t.Errorf("Remote-Groups = %q", got)
	}
}

func TestVerifyAllowsUserWithNoGroups(t *testing.T) {
	h, token := newVerifyHandler(t, "alice")
	h.groupLookup = func(string) ([]string, error) { return nil, nil }

	if rr := verify(h, "/auth/verify", "alice", token); rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
}

func TestVerifyRefusesWhenGroupsCannotBeRead(t *testing.T) {
	h, token := newVerifyHandler(t, "alice")
	h.groupLookup = func(string) ([]string, error) { return nil, errors.New("lldap down") }

	rr := verify(h, "/auth/verify", "alice", token)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", rr.Code)
	}
	if rr.Header().Get("Remote-User") != "" {
		t.Error("identity headers must not accompany a failed lookup")
	}
}

func TestVerifyRejectsDNMetacharactersInUsername(t *testing.T) {
	h, token := newVerifyHandler(t, "alice")
	h.groupLookup = func(string) ([]string, error) {
		t.Fatal("a rejected username must not reach the group lookup")
		return nil, nil
	}

	for _, username := range []string{
		"alice,x", "alice,ou=other", "alice=x", "ali\\ce", "alice\n", `al"ice`,
		" alice", "alice\t", "al*ce", "alice)(uid=*", "",
	} {
		if rr := verify(h, "/auth/verify", username, token); rr.Code != http.StatusUnauthorized {
			t.Errorf("username %q: status = %d, want 401", username, rr.Code)
		}
	}
}

func TestVerifyReportsOneSpellingOfAUsername(t *testing.T) {
	h, token := newVerifyHandler(t, "alice")
	h.groupLookup = func(string) ([]string, error) { return nil, nil }

	rr := verify(h, "/auth/verify", "ALICE", token)
	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
	if got := rr.Header().Get("Remote-User"); got != "alice" {
		t.Errorf("Remote-User = %q, want alice", got)
	}
}

func TestVerifyGroupParameterAdmitsMembers(t *testing.T) {
	h, token := newVerifyHandler(t, "alice")
	h.groupLookup = func(string) ([]string, error) { return []string{"tak_a", "editors"}, nil }

	rr := verify(h, "/auth/verify?group=editors", "alice", token)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
	if rr.Header().Get("Remote-User") != "alice" {
		t.Error("a member should get identity headers")
	}
}

func TestVerifyGroupParameterRefusesOthers(t *testing.T) {
	cases := map[string][]string{
		"other groups":   {"tak_a"},
		"no groups":      nil,
		"partial name":   {"editors_extra"},
		"different case": {"Editors"},
	}
	for name, groups := range cases {
		t.Run(name, func(t *testing.T) {
			h, token := newVerifyHandler(t, "alice")
			h.groupLookup = func(string) ([]string, error) { return groups, nil }

			rr := verify(h, "/auth/verify?group=editors", "alice", token)

			if rr.Code != http.StatusForbidden {
				t.Fatalf("status = %d, want 403", rr.Code)
			}
			if rr.Header().Get("Remote-User") != "" {
				t.Error("a refusal must not carry identity headers")
			}
		})
	}
}

func TestVerifyGroupRefusalDoesNotCountAsAFailedLogin(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	limiter := NewRateLimiter(time.Minute, 5*time.Minute, 1, fakeClock(&now))
	h, token := newVerifyHandler(t, "alice")
	h.limiter = limiter
	h.groupLookup = func(string) ([]string, error) { return nil, nil }

	verify(h, "/auth/verify?group=editors", "alice", token)

	if allowed, _ := limiter.CheckLockout(clientIP(httptest.NewRequest("GET", "/", nil))); !allowed {
		t.Error("a 403 locked the client out")
	}
}

func TestVerifyRefusesAnEnrollmentToken(t *testing.T) {
	h, _ := newVerifyHandler(t, "alice")
	token, err := h.proxy.tokens.Create("alice", 15, false)
	if err != nil {
		t.Fatal(err)
	}

	if ok, _ := h.proxy.HandleBind("uid=alice,ou=people,dc=takldap", token); !ok {
		t.Fatal("the token should still bind for TAK Server enrollment")
	}
	if rr := verify(h, "/auth/verify", "alice", token); rr.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401: a token must not log in to the monitor or Node-RED", rr.Code)
	}
}
