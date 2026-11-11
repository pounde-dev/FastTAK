package main

import (
	"errors"
	"fmt"
	"net"
	"path/filepath"
	"testing"
	"time"

	"github.com/jimlambrt/gldap"

	ldap "github.com/go-ldap/ldap/v3"
)

// freePort returns a currently-free TCP port on the loopback interface.
func freePort(t *testing.T) int {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("reserve port: %v", err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

// startTestProxy stands up the LDAP protocol listener with the real bind/search
// handlers and returns the client-facing address. The upstream LLDAP address is
// bogus on purpose: the reject-before-bind path must not reach it, and the
// authed path is expected to fail at the upstream dial (proving the gate was
// passed, not that a real search ran).
func startTestProxy(t *testing.T) (addr string, store *TokenStore) {
	t.Helper()
	return startLimitedTestProxy(t, nil)
}

// startLimitedTestProxy is startTestProxy with a bind limiter and an LLDAP stub
// that rejects every password, so a wrong password is a failure, not an error.
func startLimitedTestProxy(t *testing.T, limiter *RateLimiter) (addr string, store *TokenStore) {
	t.Helper()

	store, err := NewTokenStore(filepath.Join(t.TempDir(), "tokens.db"))
	if err != nil {
		t.Fatalf("token store: %v", err)
	}
	proxy := NewLDAPProxy(store, "127.0.0.1:1", "dc=takldap",
		"uid=adm_ldapservice,ou=people,dc=takldap", "adminpw")
	if limiter != nil {
		proxy.lldapBind = func(string, string) (bool, error) { return false, nil }
	}
	auth := newConnAuth()

	s, err := gldap.NewServer(gldap.WithOnClose(func(id int) { auth.forget(id) }))
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	r, err := gldap.NewMux()
	if err != nil {
		t.Fatalf("new mux: %v", err)
	}
	_ = r.Bind(makeBindHandler(proxy, auth, limiter))
	_ = r.Search(makeSearchHandler(proxy, auth))
	_ = r.Unbind(makeUnbindHandler())
	_ = r.DefaultRoute(makeDefaultHandler())
	if err := s.Router(r); err != nil {
		t.Fatalf("set router: %v", err)
	}

	port := freePort(t)
	addr = fmt.Sprintf("127.0.0.1:%d", port)
	go func() { _ = s.Run(addr) }()
	t.Cleanup(func() { _ = s.Stop() })

	// Wait for the listener to be ready.
	deadline := time.Now().Add(2 * time.Second)
	for !s.Ready() && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if !s.Ready() {
		t.Fatal("server never became ready")
	}
	return addr, store
}

func ldapResultCode(err error) uint16 {
	var le *ldap.Error
	if errors.As(err, &le) {
		return le.ResultCode
	}
	return 0
}

// An anonymous (unbound) connection must not be able to search — this is the
// directory-enumeration hole. The reject happens before the upstream is dialed.
func TestSearchRejectedWithoutBind(t *testing.T) {
	addr, _ := startTestProxy(t)

	conn, err := ldap.DialURL("ldap://" + addr)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()

	_, err = conn.Search(ldap.NewSearchRequest(
		"dc=takldap", ldap.ScopeWholeSubtree, ldap.NeverDerefAliases, 0, 0, false,
		"(objectClass=*)", []string{"cn"}, nil,
	))
	if err == nil {
		t.Fatal("expected anonymous search to be rejected, got success")
	}
	if code := ldapResultCode(err); code != ldap.LDAPResultInsufficientAccessRights {
		t.Fatalf("result code = %d, want InsufficientAccessRights (%d): %v",
			code, ldap.LDAPResultInsufficientAccessRights, err)
	}
}

// After a successful (token) bind, the search passes the authorization gate and
// proceeds to the upstream — which fails here because the LLDAP addr is bogus.
// The distinguishing signal is that we do NOT get InsufficientAccessRights.
func TestSearchAllowedAfterBind(t *testing.T) {
	addr, store := startTestProxy(t)

	plaintext, err := store.Create("jsmith", 15, false)
	if err != nil {
		t.Fatalf("create token: %v", err)
	}

	conn, err := ldap.DialURL("ldap://" + addr)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()

	if err := conn.Bind("uid=jsmith,ou=people,dc=takldap", plaintext); err != nil {
		t.Fatalf("token bind should succeed: %v", err)
	}

	_, err = conn.Search(ldap.NewSearchRequest(
		"dc=takldap", ldap.ScopeWholeSubtree, ldap.NeverDerefAliases, 0, 0, false,
		"(objectClass=*)", []string{"cn"}, nil,
	))
	if err == nil {
		return // upstream somehow answered; gate was passed either way
	}
	if code := ldapResultCode(err); code == ldap.LDAPResultInsufficientAccessRights {
		t.Fatalf("authenticated search was wrongly rejected by the authz gate: %v", err)
	}
}

func bindResult(t *testing.T, addr, dn, password string) error {
	t.Helper()
	conn, err := ldap.DialURL("ldap://" + addr)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()
	return conn.Bind(dn, password)
}

// Password guessing through TAK Server's port 8446 arrives here as binds.
func TestRepeatedFailedBindsLockTheNameOut(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	limiter := NewRateLimiter(time.Minute, 5*time.Minute, 3, fakeClock(&now))
	addr, store := startLimitedTestProxy(t, limiter)
	token, err := store.Create("alice", 15, false)
	if err != nil {
		t.Fatal(err)
	}

	// Spellings of one name share a count.
	for _, dn := range []string{
		"uid=alice,ou=people,dc=takldap",
		"UID=Alice,ou=people,dc=takldap",
		"uid=alice,ou=people,dc=takldap",
	} {
		if err := bindResult(t, addr, dn, "guess"); err == nil {
			t.Fatal("a wrong password bound")
		}
	}

	if err := bindResult(t, addr, "uid=alice,ou=people,dc=takldap", token); err == nil {
		t.Error("a locked-out name bound with a valid credential")
	}
	if err := bindResult(t, addr, "uid=bob,ou=people,dc=takldap", "guess"); err == nil {
		t.Fatal("a wrong password bound")
	}
	if allowed, _ := limiter.CheckLockout("bob"); !allowed {
		t.Error("one name's failures locked out another")
	}

	now = now.Add(6 * time.Minute)
	if err := bindResult(t, addr, "uid=alice,ou=people,dc=takldap", token); err != nil {
		t.Errorf("the name should bind again once the lockout ends: %v", err)
	}
}

// Locking this account would stop TAK Server reading the directory at all.
func TestTheServiceAccountIsNeverLockedOut(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	limiter := NewRateLimiter(time.Minute, 5*time.Minute, 2, fakeClock(&now))
	addr, _ := startLimitedTestProxy(t, limiter)

	for range 5 {
		_ = bindResult(t, addr, "uid=adm_ldapservice,ou=people,dc=takldap", "guess")
	}
	if allowed, _ := limiter.CheckLockout("adm_ldapservice"); !allowed {
		t.Error("the service account was locked out")
	}
}
