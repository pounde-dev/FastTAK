package main

import (
	"encoding/base64"
	"fmt"
	"log"
	"net/http"
	"regexp"
	"strings"

	ldap "github.com/go-ldap/ldap/v3"
)

type AuthHandler struct {
	proxy   *LDAPProxy
	limiter *RateLimiter // optional; when non-nil, failed auths call RecordFailure

	// groupLookup returns the user's group names. Tests replace it; everything
	// else uses fetchUserGroups.
	groupLookup func(username string) ([]string, error)
}

// NewAuthHandler creates a handler for /auth/verify.
//
// If limiter is non-nil, the handler calls limiter.RecordFailure on each 401
// response so failures (and only failures) count toward the rate-limit budget.
// Successful auths don't consume budget — important because Caddy's
// forward_auth hits this endpoint on every protected request.
func NewAuthHandler(proxy *LDAPProxy, limiter *RateLimiter) *AuthHandler {
	a := &AuthHandler{proxy: proxy, limiter: limiter}
	a.groupLookup = a.fetchUserGroups
	return a
}

// recordFailure records a failed auth against the rate limiter, if one is
// configured. Called from every 401 return path in HandleVerify.
func (a *AuthHandler) recordFailure(r *http.Request) {
	if a.limiter == nil {
		return
	}
	a.limiter.RecordFailure(clientIP(r))
}

// HandleVerify implements GET /auth/verify for Caddy forward_auth.
// Reads Basic auth header, does LDAP bind, returns 200 or 401.
func (a *AuthHandler) HandleVerify(w http.ResponseWriter, r *http.Request) {
	authHeader := r.Header.Get("Authorization")
	if authHeader == "" || !strings.HasPrefix(authHeader, "Basic ") {
		w.Header().Set("WWW-Authenticate", `Basic realm="FastTAK"`)
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		a.recordFailure(r)
		return
	}

	decoded, err := base64.StdEncoding.DecodeString(authHeader[6:])
	if err != nil {
		http.Error(w, "Invalid auth header", http.StatusBadRequest)
		return
	}

	parts := strings.SplitN(string(decoded), ":", 2)
	if len(parts) != 2 {
		http.Error(w, "Invalid auth header", http.StatusBadRequest)
		return
	}

	// LLDAP matches names without regard to case. Lowercase here so the
	// identity handed to the backends and the audit log is always the same one.
	username, password := strings.ToLower(parts[0]), parts[1]
	if !isPlainUsername(username) {
		w.Header().Set("WWW-Authenticate", `Basic realm="FastTAK"`)
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		a.recordFailure(r)
		return
	}
	bindDN := fmt.Sprintf("uid=%s,ou=people,%s", username, a.proxy.baseDN)

	ok, err := a.proxy.HandleBind(bindDN, password)
	if err != nil {
		log.Printf("auth verify error for %s: %v", username, err)
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	if !ok {
		w.Header().Set("WWW-Authenticate", `Basic realm="FastTAK"`)
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		a.recordFailure(r)
		return
	}

	// Fetch user groups from LLDAP for the Remote-Groups header
	groups, err := a.groupLookup(username)
	if err != nil {
		// Unknown groups are not the same as no groups: authorization further
		// down the chain cannot tell them apart, so refuse the request.
		log.Printf("failed to fetch groups for %s: %v", username, err)
		http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
		return
	}

	w.Header().Set("Remote-User", username)
	w.Header().Set("Remote-Groups", strings.Join(groups, ","))
	w.WriteHeader(http.StatusOK)
}

// plainUsername matches the names the monitor lets an admin create. Anything
// else could change the meaning of the bind DN built from it: the bind path
// reads only the part before the first comma, so "alice,junk" would be verified
// as alice and returned to the caller as a different identity.
var plainUsername = regexp.MustCompile(`^[a-zA-Z0-9._-]{1,64}$`)

func isPlainUsername(username string) bool {
	return plainUsername.MatchString(username)
}

// fetchUserGroups queries LLDAP for the user's group memberships.
func (a *AuthHandler) fetchUserGroups(username string) ([]string, error) {
	conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", a.proxy.lldapAddr))
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	// Bind as admin to search
	if err := conn.Bind(a.proxy.adminBindDN, a.proxy.adminBindPass); err != nil {
		return nil, fmt.Errorf("admin bind: %w", err)
	}

	searchReq := ldap.NewSearchRequest(
		fmt.Sprintf("ou=groups,%s", a.proxy.baseDN),
		ldap.ScopeWholeSubtree, ldap.NeverDerefAliases, 0, 0, false,
		fmt.Sprintf("(member=uid=%s,ou=people,%s)", ldap.EscapeFilter(username), a.proxy.baseDN),
		[]string{"cn"},
		nil,
	)

	sr, err := conn.Search(searchReq)
	if err != nil {
		return nil, err
	}

	var groups []string
	for _, entry := range sr.Entries {
		groups = append(groups, entry.GetAttributeValue("cn"))
	}
	return groups, nil
}
