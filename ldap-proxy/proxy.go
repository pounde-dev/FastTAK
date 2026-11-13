package main

import (
	"fmt"
	"log"
	"strings"

	ldap "github.com/go-ldap/ldap/v3"
)

type LDAPProxy struct {
	tokens        *TokenStore
	lldapAddr     string // e.g., "lldap:3890"
	baseDN        string // e.g., "dc=takldap"
	adminBindDN   string // DN for admin searches, e.g., "uid=adm_ldapservice,ou=people,dc=takldap"
	adminBindPass string

	// lldapBind checks a DN and password against LLDAP. Tests replace it;
	// everything else uses dialAndBind.
	lldapBind func(bindDN, password string) (bool, error)
}

func NewLDAPProxy(tokens *TokenStore, lldapAddr, baseDN, adminBindDN, adminBindPass string) *LDAPProxy {
	p := &LDAPProxy{
		tokens:        tokens,
		lldapAddr:     lldapAddr,
		baseDN:        baseDN,
		adminBindDN:   adminBindDN,
		adminBindPass: adminBindPass,
	}
	p.lldapBind = p.dialAndBind
	return p
}

// extractUsername parses a bind DN like "uid=jsmith,ou=people,dc=takldap"
// and returns the uid value. Returns empty string if unparseable.
func extractUsername(bindDN, baseDN string) string {
	dn := strings.ToLower(bindDN)
	if !strings.HasSuffix(dn, ","+strings.ToLower(baseDN)) {
		return ""
	}
	parts := strings.Split(dn, ",")
	if len(parts) < 2 {
		return ""
	}
	cnPart := parts[0]
	if !strings.HasPrefix(cnPart, "uid=") {
		return ""
	}
	return strings.TrimPrefix(cnPart, "uid=")
}

// HandleBind processes an LDAP bind request from TAK Server.
// 1. Extract username from DN
// 2. Check token store
// 3. If no token match, forward to LLDAP
//
// Enrollment tokens are accepted here and nowhere else: they exist so a device
// can enroll with TAK Server. BindPassword is the check for everything else.
func (p *LDAPProxy) HandleBind(bindDN, password string) (bool, error) {
	username := extractUsername(bindDN, p.baseDN)
	if username != "" && password != "" {
		ok, err := p.tokens.Verify(username, password)
		if err != nil {
			log.Printf("token verify error for %s: %v", username, err)
		}
		if ok {
			log.Printf("token auth success for %s", username)
			return true, nil
		}
	}

	return p.lldapBind(bindDN, password)
}

// BindPassword checks a DN and password against LLDAP only. An enrollment token
// is not a password: a QR code shown on a screen must not open the monitor or
// Node-RED to whoever photographs it.
func (p *LDAPProxy) BindPassword(bindDN, password string) (bool, error) {
	return p.lldapBind(bindDN, password)
}

func (p *LDAPProxy) dialAndBind(bindDN, password string) (bool, error) {
	// TODO: connection pooling if TAK Server concurrency increases
	conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", p.lldapAddr))
	if err != nil {
		return false, fmt.Errorf("connect to lldap: %w", err)
	}
	defer conn.Close()

	err = conn.Bind(bindDN, password)
	if err != nil {
		return false, nil // Auth failed at LLDAP
	}
	return true, nil
}

// PingLLDAP checks if LLDAP is reachable.
func (p *LDAPProxy) PingLLDAP() error {
	conn, err := ldap.DialURL(fmt.Sprintf("ldap://%s", p.lldapAddr))
	if err != nil {
		return err
	}
	conn.Close()
	return nil
}

// bindKey is the name a failed bind is counted against. It is the uid when the
// DN has the form TAK Server builds, so spellings of one name share a count,
// and the whole DN otherwise.
func bindKey(bindDN, baseDN string) string {
	if username := extractUsername(bindDN, baseDN); username != "" {
		return strings.TrimSpace(username)
	}
	key := strings.ToLower(strings.TrimSpace(bindDN))
	if len(key) > 256 {
		key = key[:256]
	}
	return key
}
