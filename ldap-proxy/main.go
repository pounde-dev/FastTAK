package main

import (
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envIntOrDefault(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func envBoolOrDefault(key string, fallback bool) bool {
	if v := os.Getenv(key); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return fallback
}

func envDurationOrDefault(key string, fallback time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return fallback
}

func main() {
	dbPath := envOrDefault("TOKEN_DB_PATH", "/data/tokens.db")
	lldapAddr := envOrDefault("LLDAP_ADDR", "lldap:3890")
	baseDN := strings.ToLower(envOrDefault("LDAP_BASE_DN", "dc=takldap"))
	ldapListenAddr := envOrDefault("LDAP_LISTEN_ADDR", ":3389")
	httpListenAddr := envOrDefault("HTTP_LISTEN_ADDR", ":8080")
	defaultTTL := envIntOrDefault("ENROLLMENT_TOKEN_TTL_MINUTES", 15)
	defaultOneTime := envBoolOrDefault("ENROLLMENT_TOKEN_ONE_TIME", true)

	// Admin credentials for search forwarding and group lookups in /auth/verify
	adminDN := envOrDefault("LDAP_ADMIN_DN", "uid=adm_ldapservice,ou=people,"+baseDN)
	adminPass := os.Getenv("LDAP_BIND_PASSWORD")
	if adminPass == "" {
		log.Fatal("LDAP_BIND_PASSWORD is required but not set")
	}

	// Shared secret authenticating the internal /tokens API. Required — an
	// unauthenticated token API lets any workload on the Docker network mint a
	// bind credential for any user. setup.sh generates it; the monitor sends it.
	tokensSecret := os.Getenv("TOKENS_API_SECRET")
	if tokensSecret == "" {
		log.Fatal("TOKENS_API_SECRET is required but not set")
	}

	// Initialize token store
	tokens, err := NewTokenStore(dbPath)
	if err != nil {
		log.Fatalf("Failed to initialize token store: %v", err)
	}

	// Initialize proxy
	proxy := NewLDAPProxy(tokens, lldapAddr, baseDN, adminDN, adminPass)

	// Rate limiter for /auth/verify — protects against brute force on LDAP auth.
	// Only counts FAILED attempts (DD-037): Caddy's forward_auth hits this on every
	// request, so counting successes would lock out legitimate users. /tokens is
	// internal-only (not Caddy-exposed). /healthz is Docker health probes.
	// Defaults: 10 failures per 5 minutes, 15-minute lockout. Configurable via env.
	rateLimitWindow := envDurationOrDefault("LDAP_RATE_LIMIT_WINDOW", 5*time.Minute)
	rateLimitLockout := envDurationOrDefault("LDAP_RATE_LIMIT_LOCKOUT", 15*time.Minute)
	rateLimitMax := envIntOrDefault("LDAP_RATE_LIMIT_MAX_ATTEMPTS", 10)
	log.Printf("rate limit: window=%s max=%d lockout=%s (counting failures only)", rateLimitWindow, rateLimitMax, rateLimitLockout)
	authRateLimit := NewRateLimiter(rateLimitWindow, rateLimitLockout, rateLimitMax, time.Now)
	// Same limits for binds on the LDAP listener, counted per account name
	// because the client address is TAK Server's. See makeBindHandler.
	bindRateLimit := NewRateLimiter(rateLimitWindow, rateLimitLockout, rateLimitMax, time.Now)

	// REST API
	tokensAPI := NewTokensAPI(tokens, defaultTTL, defaultOneTime)
	authHandler := NewAuthHandler(proxy, authRateLimit)
	healthHandler := NewHealthHandler(tokens, proxy)

	// Per-connection auth state for the LDAP protocol listener — searches are
	// only relayed for connections that have bound non-anonymously.
	connAuth := newConnAuth()

	// HTTP mux. The /tokens API is authenticated with the shared bearer secret
	// (it mints bind credentials). /auth/verify is the Caddy forward_auth hook
	// (authenticated by the user's own Basic credentials + rate limited);
	// /healthz is the Docker health probe.
	mux := http.NewServeMux()
	mux.Handle("POST /tokens", requireBearer(tokensSecret, tokensAPI))
	mux.Handle("GET /tokens/{username}", requireBearer(tokensSecret, tokensAPI))
	mux.Handle("DELETE /tokens/{username}", requireBearer(tokensSecret, tokensAPI))
	mux.Handle("GET /auth/verify", authRateLimit.Middleware(http.HandlerFunc(authHandler.HandleVerify)))
	mux.HandleFunc("GET /healthz", healthHandler.HandleHealthz)

	// Start LDAP proxy in background
	go func() {
		log.Printf("LDAP proxy listening on %s → %s", ldapListenAddr, lldapAddr)
		if err := startLDAPServer(ldapListenAddr, proxy, connAuth, bindRateLimit); err != nil {
			log.Fatalf("LDAP server error: %v", err)
		}
	}()

	// Start HTTP server
	log.Printf("HTTP server listening on %s", httpListenAddr)
	log.Fatal(http.ListenAndServe(httpListenAddr, mux))
}
