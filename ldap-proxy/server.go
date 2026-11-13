package main

// LDAP server library choice: github.com/jimlambrt/gldap
//
// Evaluated options:
//   - jimlambrt/gldap: Clean handler-based API (Mux + HandlerFunc), supports
//     bind/search/unbind natively, handles concurrent connections, actively
//     maintained. Works well alongside go-ldap/ldap/v3 for upstream forwarding.
//   - vjeantet/ldapserver: Older, less maintained, similar handler model but
//     fewer abstractions and rougher API.
//   - Raw net.Conn + BER: Maximum control but enormous implementation effort
//     for a proxy that just needs to intercept binds and relay searches.
//
// gldap wins on maintenance, API ergonomics, and the fact that we only need
// to intercept bind and forward everything else. The handler model maps
// directly to our proxy architecture.

import (
	"fmt"
	"log"
	"time"

	"github.com/jimlambrt/gldap"

	ldap "github.com/go-ldap/ldap/v3"
)

// startLDAPServer starts the LDAP protocol listener and blocks until the
// server is stopped or encounters a fatal error.
func startLDAPServer(listenAddr string, proxy *LDAPProxy, auth *connAuth, limiter *RateLimiter) error {
	// WithOnClose lets us drop per-connection auth state when a client
	// disconnects, so connAuth.authed does not grow unbounded.
	s, err := gldap.NewServer(gldap.WithOnClose(func(connID int) {
		auth.forget(connID)
	}))
	if err != nil {
		return fmt.Errorf("create gldap server: %w", err)
	}

	r, err := gldap.NewMux()
	if err != nil {
		return fmt.Errorf("create gldap mux: %w", err)
	}

	// Bind: intercept with proxy.HandleBind (token store → LLDAP fallback)
	if err := r.Bind(makeBindHandler(proxy, auth, limiter)); err != nil {
		return fmt.Errorf("register bind handler: %w", err)
	}

	// Search: forward to LLDAP, relay results back to the client
	if err := r.Search(makeSearchHandler(proxy, auth)); err != nil {
		return fmt.Errorf("register search handler: %w", err)
	}

	// Unbind: just log, gldap handles connection cleanup
	if err := r.Unbind(makeUnbindHandler()); err != nil {
		return fmt.Errorf("register unbind handler: %w", err)
	}

	// Default route: reject unsupported operations gracefully
	if err := r.DefaultRoute(makeDefaultHandler()); err != nil {
		return fmt.Errorf("register default handler: %w", err)
	}

	if err := s.Router(r); err != nil {
		return fmt.Errorf("set router: %w", err)
	}

	return s.Run(listenAddr)
}

// makeBindHandler returns a gldap handler that authenticates bind requests
// through the proxy (token check first, then LLDAP fallback).
//
// If limiter is non-nil, failed binds are counted per account name and the
// name is locked out once it passes the limit. TAK Server binds here for every
// password login on 8446, which is published to the internet, and the client's
// address is not visible from this side, so the name is the only key there is.
// Certificate logins do not bind as the user and are unaffected. The service
// account TAK Server searches with is exempt: locking it would take every
// login down, and its password is generated.
func makeBindHandler(proxy *LDAPProxy, auth *connAuth, limiter *RateLimiter) gldap.HandlerFunc {
	serviceAccount := bindKey(proxy.adminBindDN, proxy.baseDN)

	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		connID := r.ConnectionID()
		resp := r.NewBindResponse(
			gldap.WithResponseCode(gldap.ResultInvalidCredentials),
		)
		defer func() {
			_ = w.Write(resp)
		}()

		m, err := r.GetSimpleBindMessage()
		if err != nil {
			log.Printf("[conn=%d] bind: not a simple bind message: %v", connID, err)
			auth.setAuthed(connID, false)
			return
		}

		// Anonymous bind (empty DN and password) — allowed by the protocol, but
		// it authorizes nothing: the connection is not marked authenticated, so
		// a subsequent search is rejected (see makeSearchHandler).
		if m.UserName == "" && m.Password == "" {
			auth.setAuthed(connID, false)
			resp.SetResultCode(gldap.ResultSuccess)
			log.Printf("[conn=%d] bind: anonymous bind allowed (search not authorized)", connID)
			return
		}

		key := bindKey(m.UserName, proxy.baseDN)
		limited := limiter != nil && key != serviceAccount
		if limited {
			if allowed, retryAfter := limiter.CheckLockout(key); !allowed {
				auth.setAuthed(connID, false)
				log.Printf("[conn=%d] bind: %q is locked out for %s after repeated failures", connID, key, retryAfter.Round(time.Second))
				return
			}
		}

		ok, err := proxy.HandleBind(m.UserName, string(m.Password))
		if err != nil {
			log.Printf("[conn=%d] bind: error for %s: %v", connID, m.UserName, err)
			auth.setAuthed(connID, false)
			resp.SetResultCode(gldap.ResultOperationsError)
			resp.SetDiagnosticMessage("internal proxy error")
			return
		}

		if ok {
			auth.setAuthed(connID, true)
			resp.SetResultCode(gldap.ResultSuccess)
			log.Printf("[conn=%d] bind: success for %s", connID, m.UserName)
		} else {
			auth.setAuthed(connID, false)
			if limited {
				limiter.RecordFailure(key)
			}
			log.Printf("[conn=%d] bind: invalid credentials for %s", connID, m.UserName)
		}
	}
}

// makeSearchHandler returns a gldap handler that forwards search requests
// to LLDAP and relays the results back to the client.
func makeSearchHandler(proxy *LDAPProxy, auth *connAuth) gldap.HandlerFunc {
	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		connID := r.ConnectionID()
		resp := r.NewSearchDoneResponse()
		defer func() {
			_ = w.Write(resp)
		}()

		// Authorization: only relay searches for connections that completed a
		// successful non-anonymous bind. The upstream search runs as the admin
		// service account, so an unauthenticated search would leak the whole
		// directory. Reject before touching LLDAP.
		if !auth.isAuthed(connID) {
			log.Printf("[conn=%d] search: rejected — no authenticated bind on this connection", connID)
			resp.SetResultCode(gldap.ResultInsufficientAccessRights)
			resp.SetDiagnosticMessage("bind required before search")
			return
		}

		m, err := r.GetSearchMessage()
		if err != nil {
			log.Printf("[conn=%d] search: bad message: %v", connID, err)
			resp.SetResultCode(gldap.ResultOperationsError)
			return
		}

		log.Printf("[conn=%d] search: base=%q scope=%d filter=%q", connID, m.BaseDN, m.Scope, m.Filter)

		// Connect to LLDAP as admin to perform the search
		conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", proxy.lldapAddr))
		if err != nil {
			log.Printf("[conn=%d] search: connect to lldap: %v", r.ConnectionID(), err)
			resp.SetResultCode(gldap.ResultUnavailable)
			resp.SetDiagnosticMessage("upstream LDAP unavailable")
			return
		}
		defer conn.Close()

		// Bind as admin for the search
		if err := conn.Bind(proxy.adminBindDN, proxy.adminBindPass); err != nil {
			log.Printf("[conn=%d] search: admin bind to lldap: %v", r.ConnectionID(), err)
			resp.SetResultCode(gldap.ResultOperationsError)
			resp.SetDiagnosticMessage("upstream bind failed")
			return
		}

		// Build the upstream search request, faithfully forwarding all parameters
		searchReq := ldap.NewSearchRequest(
			m.BaseDN,
			int(m.Scope),
			int(m.DerefAliases),
			int(m.SizeLimit),
			int(m.TimeLimit),
			m.TypesOnly,
			m.Filter,
			m.Attributes,
			nil,
		)

		sr, err := conn.Search(searchReq)
		if err != nil {
			log.Printf("[conn=%d] upstream search error: %v", r.ConnectionID(), err)
			resp.SetResultCode(gldap.ResultOperationsError)
			resp.SetDiagnosticMessage("upstream search failed")
			return
		}

		// Relay each entry back to the client
		for _, entry := range sr.Entries {
			attrs := make(map[string][]string, len(entry.Attributes))
			for _, a := range entry.Attributes {
				attrs[a.Name] = a.Values
			}
			e := r.NewSearchResponseEntry(entry.DN, gldap.WithAttributes(attrs))
			_ = w.Write(e)
		}

		resp.SetResultCode(gldap.ResultSuccess)
	}
}

// makeUnbindHandler returns a gldap handler for unbind requests.
func makeUnbindHandler() gldap.HandlerFunc {
	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		log.Printf("[conn=%d] unbind", r.ConnectionID())
		// Nothing to do — gldap handles connection cleanup
	}
}

// makeDefaultHandler returns a handler for any unsupported LDAP operations.
// TAK Server only uses bind + search, so other operations are rejected.
func makeDefaultHandler() gldap.HandlerFunc {
	return func(w *gldap.ResponseWriter, r *gldap.Request) {
		log.Printf("[conn=%d] unsupported operation, rejecting", r.ConnectionID())
		resp := r.NewResponse(
			gldap.WithResponseCode(gldap.ResultUnwillingToPerform),
			gldap.WithDiagnosticMessage("operation not supported by proxy"),
		)
		_ = w.Write(resp)
	}
}
