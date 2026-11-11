#!/bin/sh
# FastTAK init — patches CoreConfig.xml and generates certs.
# Runs once before tak-server starts. All config in one pass.
set -e

TAK_DIR="/opt/tak"
CONFIG="${TAK_DIR}/CoreConfig.xml"
TEMPLATE="${TAK_DIR}/CoreConfig.example.xml"
CERT_DIR="${TAK_DIR}/certs"
CERT_FILES="${CERT_DIR}/files"

# ── Validate inputs ─────────────────────────────────────────────────────────

for var in SERVER_ADDRESS TAK_DB_PASSWORD; do
  eval val=\$$var
  if [ -z "$val" ]; then
    echo "[init] ERROR: $var is not set. Check your .env file." >&2
    exit 1
  fi
done

if [ ! -d "${TAK_DIR}" ]; then
  echo "[init] ERROR: ${TAK_DIR} not found. Did you run setup.sh?" >&2
  exit 1
fi

if [ -n "${SERVER_ADDRESS}" ]; then
  case "${SERVER_ADDRESS}" in
    *[!a-zA-Z0-9._-]*) echo "[init] ERROR: SERVER_ADDRESS contains invalid characters" >&2; exit 1 ;;
  esac
fi

if [ -n "${TAK_DB_PASSWORD}" ]; then
  case "${TAK_DB_PASSWORD}" in
    *[\"\'\&\<\>\|]*) echo "[init] ERROR: TAK_DB_PASSWORD contains unsafe characters (\", ', &, <, >, |)" >&2; exit 1 ;;
  esac
fi

if [ -n "${LDAP_BIND_PASSWORD}" ]; then
  case "${LDAP_BIND_PASSWORD}" in
    *[\"\'\&\<\>]*) echo "[init] ERROR: LDAP_BIND_PASSWORD contains XML-unsafe characters (\", ', &, <, >)" >&2; exit 1 ;;
  esac
fi

# Validate DEPLOY_MODE
DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"
case "${DEPLOY_MODE}" in
  direct|subdomain) ;;
  *) echo "[init] ERROR: DEPLOY_MODE must be 'direct' or 'subdomain' (got: ${DEPLOY_MODE})" >&2; exit 1 ;;
esac

# Warn if subdomain mode with IP address
if [ "${DEPLOY_MODE}" = "subdomain" ]; then
  case "${SERVER_ADDRESS}" in
    [0-9]*.[0-9]*.[0-9]*.[0-9]*)
      echo "[init] WARNING: DEPLOY_MODE=subdomain with an IP address (${SERVER_ADDRESS})." >&2
      echo "[init]          Subdomain mode requires DNS. Did you mean DEPLOY_MODE=direct?" >&2
      ;;
  esac
fi

# ── 1. Create CoreConfig.xml from template if needed ─────────────────────────

if [ ! -f "${CONFIG}" ]; then
  if [ -f "${TEMPLATE}" ]; then
    echo "[init] Creating CoreConfig.xml from template"
    cp "${TEMPLATE}" "${CONFIG}"
  else
    echo "[init] ERROR: No CoreConfig template found at ${TEMPLATE}" >&2
    exit 1
  fi
fi

# ── 2. Patch database connection ────────────────────────────────────────────

sed -i 's|jdbc:postgresql://[^/]*/cot|jdbc:postgresql://tak-database:5432/cot|g' "${CONFIG}"

if [ -n "${TAK_DB_PASSWORD}" ]; then
  sed -i '/<connection /s|password="[^"]*"|password="'"${TAK_DB_PASSWORD}"'"|' "${CONFIG}"
  echo "[init] Database password set"
fi

# ── 3. Enable admin UI on 8446 ──────────────────────────────────────────────

if ! grep -q 'enableAdminUI="true"' "${CONFIG}" 2>/dev/null; then
  sed -i 's|_name="cert_https"|_name="cert_https" enableAdminUI="true" enableWebtak="true"|g' "${CONFIG}"
  echo "[init] Admin UI enabled on 8446"
fi

# ── 4. Generate certificates if none exist ───────────────────────────────────

if [ ! -f "${CERT_FILES}/ca.pem" ]; then
  echo "[init] No CA found — generating certificates..."
  export STATE="${STATE:-XX}"
  export CITY="${CITY:-Default}"
  export ORGANIZATIONAL_UNIT="${ORGANIZATIONAL_UNIT:-FastTAK}"
  cd "${CERT_DIR}"
  bash ./makeRootCa.sh --ca-name FastTAK-CA 2>&1 | tail -3
  bash ./makeCert.sh server takserver 2>&1 | tail -2
  bash ./makeCert.sh client svc_fasttakapi 2>&1 | tail -2
  cd /
  echo "[init] Certificates generated"
fi

# Generate missing svc_fasttakapi cert (handles upgrades where CA already exists).
# This is the only bootstrap-time service cert — other service accounts (e.g.,
# those used by Node-RED flows) are created on demand via the Service Accounts
# dashboard.
if [ -f "${CERT_FILES}/ca.pem" ] && [ ! -f "${CERT_FILES}/svc_fasttakapi.pem" ]; then
  export STATE="${STATE:-XX}"
  export CITY="${CITY:-Default}"
  export ORGANIZATIONAL_UNIT="${ORGANIZATIONAL_UNIT:-FastTAK}"
  echo "[init] Generating missing cert: svc_fasttakapi"
  cd "${CERT_DIR}" && bash ./makeCert.sh client svc_fasttakapi 2>&1 | tail -2
  cd /
fi

# ── 5. Create CA signing keystore ────────────────────────────────────────────

if [ ! -f "${CERT_FILES}/ca-signing.jks" ] && [ -f "${CERT_FILES}/ca.pem" ] && [ -f "${CERT_FILES}/ca-do-not-share.key" ]; then
  echo "[init] Creating CA signing keystore..."
  openssl pkcs12 -export \
    -in "${CERT_FILES}/ca.pem" \
    -inkey "${CERT_FILES}/ca-do-not-share.key" \
    -passin pass:atakatak \
    -out "${CERT_FILES}/ca-signing.p12" \
    -name ca \
    -passout pass:atakatak 2>/dev/null && \
  keytool -importkeystore \
    -srckeystore "${CERT_FILES}/ca-signing.p12" \
    -srcstoretype PKCS12 \
    -srcstorepass atakatak \
    -destkeystore "${CERT_FILES}/ca-signing.jks" \
    -deststoretype JKS \
    -deststorepass atakatak \
    -noprompt 2>/dev/null && \
  rm -f "${CERT_FILES}/ca-signing.p12" && \
  echo "[init] CA signing keystore created" || \
  echo "[init] WARNING: Failed to create CA signing keystore"
fi

# ── 6. Add certificateSigning block ──────────────────────────────────────────

if ! grep -q '<certificateSigning CA="TAKServer">' "${CONFIG}" 2>/dev/null && [ -f "${CERT_FILES}/ca-signing.jks" ]; then
  echo "[init] Adding certificateSigning block..."
  sed -i '/<\/Configuration>/i\
    <certificateSigning CA="TAKServer">\
        <certificateConfig>\
            <nameEntries>\
                <nameEntry name="O" value="FastTAK"/>\
                <nameEntry name="OU" value="TAK"/>\
            </nameEntries>\
        </certificateConfig>\
        <TAKServerCAConfig keystore="JKS" keystoreFile="certs/files/ca-signing.jks" keystorePass="atakatak" validityDays="365" signatureAlg="SHA256WithRSA"/>\
    </certificateSigning>' "${CONFIG}"
fi

# ── 7. Create server cert for SERVER_ADDRESS ───────────────────────────────
# Generate a server cert matching SERVER_ADDRESS and replace the default
# takserver cert. Skip for localhost (use default takserver cert).

if [ -n "${SERVER_ADDRESS}" ] && [ "${SERVER_ADDRESS}" != "localhost" ] && [ -f "${CERT_FILES}/ca.pem" ]; then
  if [ ! -f "${CERT_FILES}/${SERVER_ADDRESS}.jks" ]; then
    echo "[init] Creating server cert for ${SERVER_ADDRESS}..."
    export STATE="${STATE:-XX}"
    export CITY="${CITY:-Default}"
    export ORGANIZATIONAL_UNIT="${ORGANIZATIONAL_UNIT:-FastTAK}"
    cd "${CERT_DIR}" && bash ./makeCert.sh server "${SERVER_ADDRESS}" 2>&1 | tail -2
    cd /
  fi

  if [ -f "${CERT_FILES}/${SERVER_ADDRESS}.jks" ]; then
    echo "[init] Installing ${SERVER_ADDRESS} cert as default server cert"
    cp -f "${CERT_FILES}/${SERVER_ADDRESS}.jks" "${CERT_FILES}/takserver.jks"
    cp -f "${CERT_FILES}/${SERVER_ADDRESS}.pem" "${CERT_FILES}/takserver.pem"
    [ -f "${CERT_FILES}/${SERVER_ADDRESS}.p12" ] && cp -f "${CERT_FILES}/${SERVER_ADDRESS}.p12" "${CERT_FILES}/takserver.p12"
    keytool -changealias -keystore "${CERT_FILES}/takserver.jks" \
      -storepass atakatak -alias "${SERVER_ADDRESS}" -destalias takserver 2>/dev/null || true
  fi
fi

# ── 8. Patch LDAP auth (if LDAP vars are set) ────────────────────────────────

if [ -n "${LDAP_BIND_PASSWORD}" ]; then
  LDAP_HOST="${LDAP_HOST:-ldap-proxy}"
  LDAP_BASE_DN="${LDAP_BASE_DN:-DC=takldap}"
  BASE_DN_LOWER=$(echo "${LDAP_BASE_DN}" | tr '[:upper:]' '[:lower:]')

  if ! grep -q 'adm_ldapservice' "${CONFIG}" 2>/dev/null; then
    echo "[init] Adding LDAP auth block..."

    AUTH_BLOCK='    <auth default="ldap" x509groups="true" x509addAnonymous="false" x509useGroupCache="true" x509useGroupCacheDefaultActive="true" x509useGroupCacheRequiresExtKeyUsage="false" x509checkRevocation="true">\
        <ldap url="ldap://'"${LDAP_HOST}"':3389" userstring="uid={username},ou=people,'"${BASE_DN_LOWER}"'" updateinterval="30" groupprefix="cn=tak_" groupNameExtractorRegex="cn=tak_(.*?)(?:,|$)" serviceAccountDN="uid=adm_ldapservice,ou=people,'"${BASE_DN_LOWER}"'" serviceAccountCredential="'"${LDAP_BIND_PASSWORD}"'" groupBaseRDN="ou=groups,'"${BASE_DN_LOWER}"'" userBaseRDN="ou=people,'"${BASE_DN_LOWER}"'" dnAttributeName="DN" nameAttr="CN" adminGroup="ROLE_ADMIN"/>\
    </auth>'

    # shellcheck disable=SC1003  # intentional sed quoting for XML block replacement
    sed -i '/<auth/,/<\/auth>/c\'"${AUTH_BLOCK}" "${CONFIG}"
    echo "[init] LDAP auth configured"
  else
    # LDAP already configured — update password if changed
    CURRENT_PASS=$(grep -o 'serviceAccountCredential="[^"]*"' "${CONFIG}" | sed 's/.*="//;s/"//')
    if [ -n "$CURRENT_PASS" ] && [ "$CURRENT_PASS" != "${LDAP_BIND_PASSWORD}" ]; then
      sed -i 's|serviceAccountCredential="[^"]*"|serviceAccountCredential="'"${LDAP_BIND_PASSWORD}"'"|' "${CONFIG}"
      echo "[init] LDAP password updated"
    else
      echo "[init] LDAP already configured"
    fi
  fi
fi

# Backfill x509useGroupCacheRequiresExtKeyUsage="false" on existing
# deployments. See DD-042: TAK Server 5.6 silently disables channel
# toggles when this attribute is absent (defaults to true) and the
# client cert lacks an undocumented EKU OID.
if ! grep -q 'x509useGroupCacheRequiresExtKeyUsage' "${CONFIG}"; then
  echo "[init] Backfilling x509useGroupCacheRequiresExtKeyUsage=\"false\" on auth element"
  sed -i 's|<auth |<auth x509useGroupCacheRequiresExtKeyUsage="false" |' "${CONFIG}"
fi

# ── 9. Template retention policy ────────────────────────────────────────────
# Configure TAK Server's built-in retention service from .env variables.
# Unset values default to null (keep forever), matching stock TAK Server.

# Validate numeric values
for _rvar in COT_RETENTION_DAYS GEOCHAT_RETENTION_DAYS; do
  eval _rval=\$$_rvar
  if [ -n "$_rval" ]; then
    case "$_rval" in
      *[!0-9]*) echo "[init] ERROR: $_rvar must be numeric" >&2; exit 1 ;;
    esac
  fi
done

# Validate cron expression (basic character safety)
if [ -n "$RETENTION_CRON" ]; then
  case "$RETENTION_CRON" in
    *[!0-9\ *?,/-]*) echo "[init] ERROR: RETENTION_CRON contains invalid characters" >&2; exit 1 ;;
  esac
fi

# Auto-enable cron when any retention days are set
# Override with RETENTION_CRON in .env (Quartz cron syntax)
if [ -n "$COT_RETENTION_DAYS" ] || [ -n "$GEOCHAT_RETENTION_DAYS" ]; then
    RETENTION_CRON="${RETENTION_CRON:-0 0 3 * * ?}"
fi

# Only overwrite retention YAML files if env vars are explicitly set.
# This preserves manual edits to the YAML for operators who configure
# retention directly instead of through .env.
if [ -n "$COT_RETENTION_DAYS" ] || [ -n "$GEOCHAT_RETENTION_DAYS" ] || [ -n "$RETENTION_CRON" ]; then
    COT_RET="${COT_RETENTION_DAYS:-null}"
    GEOCHAT_RET="${GEOCHAT_RETENTION_DAYS:-null}"
    RET_CRON="${RETENTION_CRON:--}"

    cat > "${TAK_DIR}/conf/retention/retention-policy.yml" << EOF
---
dataRetentionMap:
  cot: ${COT_RET}
  files: null
  missionpackages: null
  missions: null
  geochat: ${GEOCHAT_RET}
EOF

    cat > "${TAK_DIR}/conf/retention/retention-service.yml" << EOF
---
cronExpression: "${RET_CRON}"
EOF

    echo "[init] Retention policy templated (cot=${COT_RET}, geochat=${GEOCHAT_RET}, cron=${RET_CRON})"
else
    echo "[init] Retention policy unchanged (no .env overrides set)"
fi

# ── 10. Upgrade .p12 files to modern ciphers ───────────────────────────────────
# TAK's cert tools use legacy RC2-40 which modern OpenSSL 3.x rejects.
# Re-export all .p12 files with AES-256-CBC after all cert generation is done.

for p12 in "${CERT_FILES}"/*.p12; do
  [ -f "$p12" ] || continue
  # Check if already modern (openssl can read it without -legacy)
  if openssl pkcs12 -in "$p12" -passin pass:atakatak -nokeys -out /dev/null 2>/dev/null; then
    continue
  fi
  # Try extracting with -legacy flag and re-exporting with modern ciphers
  if openssl pkcs12 -in "$p12" -passin pass:atakatak -nokeys -out /tmp/cert.pem -legacy 2>/dev/null; then
    if openssl pkcs12 -in "$p12" -passin pass:atakatak -nocerts -nodes -out /tmp/key.pem -legacy 2>/dev/null && [ -s /tmp/key.pem ]; then
      # Has private key — full re-export
      openssl pkcs12 -export -in /tmp/cert.pem -inkey /tmp/key.pem -out "$p12" \
        -passout pass:atakatak -certpbe AES-256-CBC -keypbe AES-256-CBC -macalg SHA256 2>/dev/null
    else
      # Cert-only (e.g., truststore) — re-export without key
      openssl pkcs12 -export -in /tmp/cert.pem -nokeys -out "$p12" \
        -passout pass:atakatak -certpbe AES-256-CBC -macalg SHA256 2>/dev/null
    fi
    rm -f /tmp/cert.pem /tmp/key.pem
  fi
done

# ── 11. Generate Caddyfile ─────────────────────────────────────────────────

CADDY_DIR="${TAK_DIR}/caddy"
mkdir -p "${CADDY_DIR}"

# Full copy_headers list matching current Caddyfile
COPY_HEADERS="Remote-User Remote-Groups"

if [ "${DEPLOY_MODE}" = "direct" ]; then
  NODERED_PORT="${NODERED_PORT:-1880}"
  MONITOR_PORT="${MONITOR_PORT:-8180}"
  TAKSERVER_ADMIN_PORT="${TAKSERVER_ADMIN_PORT:-8446}"
  MEDIAMTX_PORT="${MEDIAMTX_PORT:-8888}"

  cat > "${CADDY_DIR}/Caddyfile" << CADDYEOF
# Generated by init-config — do not edit manually.
# Mode: direct | Address: ${SERVER_ADDRESS}
{
    default_sni ${SERVER_ADDRESS}
}

# TAK Server web admin (proxied to internal 8446, self-signed cert upstream)
https://${SERVER_ADDRESS}:${TAKSERVER_ADMIN_PORT} {
    tls internal
    reverse_proxy tak-server:8446 {
        transport http {
            tls
            tls_insecure_skip_verify
        }
        header_down Location tak-server:8446 ${SERVER_ADDRESS}:${TAKSERVER_ADMIN_PORT}
        header_down Location http:// https://
    }
}

# MediaMTX streaming
https://${SERVER_ADDRESS}:${MEDIAMTX_PORT} {
    tls internal
    reverse_proxy mediamtx:8888
}

# Bare root → FastTAK Monitor (TAK Portal removed; the monitor is the management UI)
https://${SERVER_ADDRESS} {
    tls internal
    redir https://${SERVER_ADDRESS}:${MONITOR_PORT}{uri}
}

# Node-RED with forward auth
https://${SERVER_ADDRESS}:${NODERED_PORT} {
    tls internal
    route {
        # Caddy before 2.11.2 let a client-sent copy of these through when
        # ldap-proxy returned them empty, so drop them on every version.
        request_header -Remote-User
        request_header -Remote-Groups
        forward_auth ldap-proxy:8080 {
            uri /auth/verify
        }
        reverse_proxy nodered:1880 {
            header_up -Authorization
        }
    }
}

# FastTAK Monitor with forward auth
https://${SERVER_ADDRESS}:${MONITOR_PORT} {
    tls internal
    route {
        # Caddy before 2.11.2 let a client-sent copy of these through when
        # ldap-proxy returned them empty, so drop them on every version.
        request_header -Remote-User
        request_header -Remote-Groups
        forward_auth ldap-proxy:8080 {
            uri /auth/verify
            copy_headers ${COPY_HEADERS}
        }
        reverse_proxy monitor:8080 {
            header_up -Authorization
        }
    }
}
CADDYEOF

else
  # subdomain mode — reproduces the original static Caddyfile structure
  TAKSERVER_SUBDOMAIN="${TAKSERVER_SUBDOMAIN:-takserver}"
  MEDIAMTX_SUBDOMAIN="${MEDIAMTX_SUBDOMAIN:-stream}"
  NODERED_SUBDOMAIN="${NODERED_SUBDOMAIN:-nodered}"
  MONITOR_SUBDOMAIN="${MONITOR_SUBDOMAIN:-monitor}"

  cat > "${CADDY_DIR}/Caddyfile" << CADDYEOF
# Generated by init-config — do not edit manually.
# Mode: subdomain | Address: ${SERVER_ADDRESS}

# TAK Server web admin (self-signed cert internally)
${TAKSERVER_SUBDOMAIN}.${SERVER_ADDRESS} {
    reverse_proxy tak-server:8446 {
        transport http {
            tls
            tls_insecure_skip_verify
        }
        header_down Location tak-server:8446 ${TAKSERVER_SUBDOMAIN}.${SERVER_ADDRESS}
        header_down Location http:// https://
    }
}

# MediaMTX streaming
${MEDIAMTX_SUBDOMAIN}.${SERVER_ADDRESS} {
    reverse_proxy mediamtx:8888
}

# Node-RED with forward auth
${NODERED_SUBDOMAIN}.${SERVER_ADDRESS} {
    route {
        # Caddy before 2.11.2 let a client-sent copy of these through when
        # ldap-proxy returned them empty, so drop them on every version.
        request_header -Remote-User
        request_header -Remote-Groups
        forward_auth ldap-proxy:8080 {
            uri /auth/verify
        }
        reverse_proxy nodered:1880 {
            header_up -Authorization
        }
    }
}

# FastTAK Monitor with forward auth
${MONITOR_SUBDOMAIN}.${SERVER_ADDRESS} {
    route {
        # Caddy before 2.11.2 let a client-sent copy of these through when
        # ldap-proxy returned them empty, so drop them on every version.
        request_header -Remote-User
        request_header -Remote-Groups
        forward_auth ldap-proxy:8080 {
            uri /auth/verify
            copy_headers ${COPY_HEADERS}
        }
        reverse_proxy monitor:8080 {
            header_up -Authorization
        }
    }
}
CADDYEOF

fi

echo "[init] Caddyfile generated (mode=${DEPLOY_MODE})"

echo "[init] CoreConfig.xml ready"
