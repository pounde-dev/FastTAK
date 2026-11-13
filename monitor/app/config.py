"""Read configuration from environment variables (passed from .env via compose)."""

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Service versions — for update comparison
    tak_version: str = ""
    lldap_version: str = ""
    mediamtx_version: str = ""
    nodered_version: str = ""

    # Deployment
    server_address: str = "localhost"
    deploy_mode: str = "direct"

    # Subdomains (subdomain mode)
    takserver_subdomain: str = "takserver"
    mediamtx_subdomain: str = "stream"
    nodered_subdomain: str = "nodered"
    monitor_subdomain: str = "monitor"

    # Ports (direct mode)
    nodered_port: int = 1880
    monitor_port: int = 8180
    takserver_admin_port: int = 8446
    mediamtx_port: int = 8888

    # Alert email (SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_email: str = ""  # Recipient for health alerts

    # Alert SMS (optional)
    sms_provider: str = ""  # "twilio" or "brevo"
    sms_api_key: str = ""
    sms_from: str = ""
    sms_to: str = ""  # Comma-separated phone numbers

    # Database
    tak_db_password: str = ""
    tak_db_url: str = ""  # Override: postgresql://user:pass@host:port/db

    # Audit/events database (uses app-db; see #13)
    fastak_db_password: str = ""
    fastak_db_url: str = ""  # Override: postgresql://user:pass@host:port/db
    app_db_host: str = "app-db"
    app_db_user: str = "fastak"  # matches docker-compose.yml POSTGRES_USER

    # Monitor settings
    update_check_interval: int = 21600  # 6 hours

    # LLDAP / identity
    lldap_url: str = "http://lldap:17170"
    ldap_proxy_url: str = "http://ldap-proxy:8080"
    ldap_admin_password: str = ""
    # Shared secret for the ldap-proxy /tokens API (TOKENS_API_SECRET). Sent as
    # a bearer token on every proxy request; the proxy rejects calls without it.
    ldap_proxy_secret: str = ""
    # Shared secret proving a request came through Caddy (MONITOR_PROXY_SECRET).
    # Without it, Remote-User and Remote-Groups are ignored (DD-059).
    monitor_proxy_secret: str = ""

    # TAK Server API (mTLS)
    tak_server_url: str = "https://tak-server:8443"
    tak_api_cert_path: str = ""
    tak_api_cert_password: str = "atakatak"

    # User management
    users_hidden_prefixes: str = "adm_,svc_,ma-"
    user_expiry_check_interval: int = 60  # seconds
    enrollment_token_ttl_minutes: int = 15
    # ONE_TIME=False because TAK Server's LdapAuthenticator binds as the user
    # twice during enrollment (auth + group assignment). A one-time token gets
    # consumed on the first bind, failing the second. TTL is the security
    # boundary instead. Flip to True if TAK Server changes this behavior.
    enrollment_token_one_time: bool = False
    tak_enrollment_port: int = 8446

    # CoT type allowlist for the "Recently seen" LKP card. Comma-separated
    # case-insensitive prefixes matched against cot_router.cot_type via ILIKE.
    # Default `a-` covers all atoms (ground/air/sea/etc.). Narrow via env if
    # aircraft (`a-?-A-*`) telemetry overwhelms human tracks.
    lkp_cot_type_prefixes: str = "a-"
    # Require <contact endpoint="..."> in the CoT detail XML (or roster
    # membership, which implies TAK Server applied the same gate) before
    # rendering a row. Mirrors ATAK's own contact-registry rule and excludes
    # passive feeds like ADS-B. Flip to false to include those feeds.
    lkp_require_tak_client: bool = True

    model_config = ConfigDict(extra="ignore")

    @property
    def lkp_cot_type_prefixes_list(self) -> list[str]:
        return [p.strip().lower() for p in self.lkp_cot_type_prefixes.split(",") if p.strip()]


settings = Settings()
