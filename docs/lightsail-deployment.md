# Deploying FastTAK to AWS Lightsail

End-to-end walkthrough for standing up a production FastTAK deployment on AWS
Lightsail. Targets a single 16 GB / 4 vCPU Ubuntu 22.04 instance at ~$80/mo
with public internet reachability, automatic Let's Encrypt TLS, and optional
Tailscale access for administrators.

This guide assumes FastTAK's default hardening posture from DD-033 through
DD-036 (auto-generated admin password, per-container memory caps, LDAP rate
limit, multi-arch image support).

## Prerequisites

Before you start, you need:

- **AWS account** with billing enabled
- **Domain name** with DNS you can edit (any registrar works — Cloudflare, Route53, Namecheap, etc.)
- **TAK Server release zip** from [tak.gov](https://tak.gov) — download the latest `takserver-docker-X.X-RELEASE-X.zip`
- **SSH key pair** — public key ready to upload to Lightsail, private key on your local machine
- **~30 minutes** once Lightsail is provisioned

This guide uses `tak.example.com` as the placeholder subdomain. Replace it with
your actual subdomain throughout.

## 1. Create the Lightsail instance

1. Open the [Lightsail console](https://lightsail.aws.amazon.com/).
2. Click **Create instance**.
3. Configure:

| Field                          | Value                                                                             |
| ------------------------------ | --------------------------------------------------------------------------------- |
| **Instance location**          | Closest AWS region to your users (e.g., `us-east-1` Virginia, `us-west-2` Oregon) |
| **Platform**                   | Linux/Unix                                                                        |
| **Blueprint**                  | OS Only → **Ubuntu 22.04 LTS**                                                    |
| **SSH key pair**               | Upload your existing public key (don't generate a new one in console)             |
| **Enable Automatic Snapshots** | Optional — recommended for production (daily, ~$4/mo extra)                       |
| **Instance plan**              | **$80/mo: 16 GB RAM, 4 vCPUs, 320 GB SSD, 5 TB transfer**                         |
| **Instance name**              | `fasttak-prod`                                                                    |

1. Click **Create instance**. Provisioning takes ~30 seconds.

## 2. Reserve a static IP

Lightsail assigns an ephemeral IP by default. Reserve a static one so DNS
stays valid across reboots.

1. Click **Networking** at the top of the Lightsail console.
2. Click **Create static IP**.
3. **Region** must match your instance's region.
4. **Attach to an instance** → select `fasttak-prod`.
5. Name it `fasttak-prod-ip`.
6. Click **Create**.

Note the assigned IPv4 address — you'll use it in DNS next. Static IPs are
free while attached to a running instance.

## 3. Configure the Lightsail firewall

The instance's network firewall is separate from any OS-level firewall. By
default Lightsail only allows SSH (22) and HTTP (80). You need to open the
TAK-specific ports.

1. Go to **Instances → fasttak-prod → Networking → IPv4 Firewall**.
2. Click **+ Add rule** for each of the following:

| Application | Protocol | Port | Source                                                             |
| ----------- | -------- | ---- | ------------------------------------------------------------------ |
| SSH         | TCP      | 22   | **Restricted to your IP** (click the pencil icon → your public IP) |
| HTTP        | TCP      | 80   | Anywhere                                                           |
| HTTPS       | TCP      | 443  | Anywhere                                                           |
| Custom      | UDP      | 443  | Anywhere                                                           |
| Custom      | TCP      | 8443 | Anywhere                                                           |
| Custom      | TCP      | 8446 | Anywhere                                                           |
| Custom      | TCP      | 8089 | Anywhere                                                           |

Port 8443 is TAK Server's HTTPS endpoint. Port 8446 is the enrollment port
(`TAK_ENROLLMENT_PORT` in `.env`) — the monitor's generated enrollment URLs
target this port directly, so it must be publicly reachable in addition to
being bound on the Docker host (see step 8). Port 8089 is the CoT streaming
endpoint (mTLS). All three require a valid client certificate issued by your
TAK CA — public exposure is safe because the cert is the credential.

Ports 80/443 are HTTP/HTTPS — HTTP is needed for Let's Encrypt's HTTP-01
challenge during cert acquisition; Caddy upgrades all HTTP traffic to HTTPS.

UDP 443 carries HTTP/3. Caddy advertises it on every HTTPS response; without
the UDP rule browsers try it, fail, and fall back to TCP on every page load.

Don't open 1935 or 8554 — MediaMTX is intentionally kept internal (see step 7).

## 4. DNS

Point your domain at the static IP before starting the stack — Caddy's
Let's Encrypt cert acquisition will fail if DNS isn't propagated.

### Option A — Wildcard (recommended)

If your DNS provider supports wildcard records:

```
tak.example.com     A   <static-ip>
*.tak.example.com   A   <static-ip>
```

Two records, covers every subdomain FastTAK uses.

### Option B — Explicit records

If wildcards aren't an option:

```
tak.example.com             A   <static-ip>
monitor.tak.example.com     A   <static-ip>
nodered.tak.example.com     A   <static-ip>
takserver.tak.example.com   A   <static-ip>
```

`stream.tak.example.com` is optional — MediaMTX isn't publicly exposed, so
nothing answers on that subdomain, but creating the record is harmless.

### TTL

Set TTL to **60-300 seconds** during initial deployment so you can iterate if
something's wrong. Raise to 3600+ once the deployment is stable.

### Verify propagation

Before proceeding, confirm DNS has propagated:

```bash
dig +short tak.example.com
dig +short monitor.tak.example.com
```

Both should return your static IP. If they don't, wait a few minutes and
re-check. Propagation typically takes 1-5 minutes with a low TTL, but can be
longer depending on your registrar.

## 5. SSH to the instance

```bash
ssh ubuntu@<static-ip>
```

Default user on Lightsail's Ubuntu 22.04 is `ubuntu`. You'll land in the home
directory.

Update the OS:

```bash
sudo apt update && sudo apt upgrade -y
```

A reboot may be required if kernel updates land. If `/var/run/reboot-required`
exists after the upgrade, run `sudo reboot` and SSH back in.

## 6. Install Docker

```bash
# Install prerequisites (unzip/openssl/netcat are needed by FastTAK's
# setup.sh and start.sh; Ubuntu Minimal doesn't ship them by default)
sudo apt install -y ca-certificates curl gnupg git unzip openssl netcat-openbsd

# Add Docker's official GPG key and apt repo
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Run docker without sudo
sudo usermod -aG docker $USER
```

Log out and back in for the group membership to take effect:

```bash
exit
# then SSH back in
ssh ubuntu@<static-ip>
```

Verify:

```bash
docker run --rm hello-world
docker compose version
```

## 7. Clone FastTAK and upload the TAK zip

```bash
cd ~
git clone https://github.com/pounde/FastTAK.git
cd FastTAK
```

Upload the TAK Server release zip from your local machine. From a new terminal
on your local machine:

```bash
scp ~/Downloads/takserver-docker-X.X-RELEASE-X.zip ubuntu@<static-ip>:~/FastTAK/
```

Back on the Lightsail instance, verify:

```bash
ls -lh ~/FastTAK/takserver-docker-*.zip
```

Should show the zip (~600 MB).

## 8. Create the local override (no public video)

This deployment keeps MediaMTX running internally for Node-RED's video path
registration API, but removes its public port bindings. ATAK clients that
need to view video feeds would need a VPN — see the optional Tailscale
section below.

Create `docker-compose.override.yml` in the FastTAK directory. Compose loads
that filename automatically, and FastTAK's entry points re-append it last in
every mode, so it cannot be dropped:

```bash
cat > ~/FastTAK/docker-compose.override.yml <<'EOF'
# Deployment-specific override — not tracked in git.
#
# - Binds tak-server's 8446 (enrollment) to the host. In subdomain mode the
#   base compose file only routes 8446 through Caddy internally, but the
#   monitor generates enrollment URLs that target SERVER_ADDRESS:8446
#   directly. Without the host binding, enrollment connections fail.
# - Removes MediaMTX's public port bindings so video ingress/egress is
#   reachable only from inside the Docker network (and, if installed,
#   the Tailscale tailnet via the Tailscale interface).
services:
  tak-server:
    ports:
      - "8446:8446"
  mediamtx:
    ports: !reset []
EOF
```

Nothing further is needed — Compose picks `docker-compose.override.yml` up on
its own.

!!! warning "Do not set `COMPOSE_FILE` to a differently-named file"
    An earlier version of this guide used `docker-compose.local.yml` with
    `COMPOSE_FILE` exported from `~/.bashrc`. That silently breaks: `./start.sh`
    and `just up` overwrite `COMPOSE_FILE` whenever they need the direct-mode or
    capture overlay, so the file is dropped and MediaMTX's public ports come
    back — unauthenticated — with the stack reporting success.

    If you followed that pattern, rename the file to
    `docker-compose.override.yml` and remove the `COMPOSE_FILE` export from your
    shell init.

## 9. Run just setup

```bash
cd ~/FastTAK
just setup takserver-docker-X.X-RELEASE-X.zip
```

This:

- Extracts the TAK release into `tak/`
- Builds the `takserver` and `takserver-database` images
- Creates `.env` from `.env.example`
- Auto-generates random passwords for `TAK_DB_PASSWORD`, `APP_DB_PASSWORD`,
  `LDAP_BIND_PASSWORD`, and `TAK_WEBADMIN_PASSWORD` (DD-033)

Setup takes a few minutes. The image build is the slowest step.

## 10. Configure .env

```bash
vim ~/FastTAK/.env
```

Edit two values:

```bash
SERVER_ADDRESS=tak.example.com   # your actual subdomain
DEPLOY_MODE=subdomain            # not direct — you want Caddy to do LE
```

Leave everything else at defaults unless you have a specific reason to change
it. The auto-generated passwords are already populated.

Note the generated admin password so you can log in later:

```bash
grep TAK_WEBADMIN_PASSWORD ~/FastTAK/.env
```

## 11. Start the stack

```bash
cd ~/FastTAK
just up
```

First boot does a lot:

- Extracts and patches `CoreConfig.xml`
- Generates the TAK CA + server cert
- Bootstraps LLDAP with the webadmin user
- Caddy acquires Let's Encrypt certs for every subdomain in `.env`
- TAK Server starts (the slowest step, ~2-3 minutes)

Expect 5-8 minutes total for everything to reach healthy state. `start.sh`
waits for `tak-server` health before declaring success.

If `start.sh` reports failure, check the logs of the service it names:

```bash
docker compose logs caddy         # Let's Encrypt issues
docker compose logs tak-server    # TAK startup issues
docker compose logs init-config   # first-boot bootstrapping
```

## 12. Verify the deployment

From your local machine (not the instance):

1. **Monitor access:**

   ```bash
   curl -I https://monitor.tak.example.com
   ```

   Should return `200 OK` or `302` with a valid Let's Encrypt cert. If you
   get a cert warning, DNS hasn't propagated yet — wait and retry.

2. **Browser login:** open `https://monitor.tak.example.com` in a browser. Log
   in as `webadmin` with the password from `.env`. The health grid should show
   all services healthy.

3. **Enroll a test user:**
   - From the monitor Users page, create a user (New User)
   - Click Enroll to generate an enrollment QR (and `tak://` URL)
   - Scan with an ATAK client on your phone
   - Watch the client appear in the TAK Server admin UI

## 13. [Optional] Install Tailscale

Tailscale adds a mesh VPN so administrators can reach services that aren't
publicly exposed (MediaMTX video feeds, internal-only endpoints) and optionally
reach the whole box via its Tailscale hostname.

This is additive — public HTTPS and TAK ports continue to work. Tailscale just
adds a second private path.

### Install

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

### Authenticate and tag

```bash
sudo tailscale up --ssh --advertise-tags=tag:fasttak-server
```

The command prints a URL. Open it in a browser, log into your Tailscale
account, and approve the machine. The `--ssh` flag lets Tailscale manage SSH
access via ACLs (optional but convenient).

The `tag:fasttak-server` lets you write ACLs like "team members can reach
`tag:fasttak-server:443,8089,8443` but nothing else."

### ACL (in the Tailscale admin console)

Add tag ownership and a restrictive ACL. Open
[Access Controls](https://login.tailscale.com/admin/acls) and edit the policy:

```json
{
  "tagOwners": {
    "tag:fasttak-server": ["autogroup:admin"]
  },
  "groups": {
    "group:fasttak-users": ["alice@example.com", "bob@example.com"]
  },
  "acls": [
    // Admin — full access
    {
      "action": "accept",
      "src": ["autogroup:admin"],
      "dst": ["*:*"]
    },
    // Team members — TAK ports plus MediaMTX video (1935, 8554) via Tailscale only
    {
      "action": "accept",
      "src": ["group:fasttak-users"],
      "dst": ["tag:fasttak-server:443,8089,8443,1935,8554"]
    }
  ]
}
```

### MediaMTX over Tailscale

Because MediaMTX's public ports are removed via `docker-compose.override.yml`,
video is only reachable through the Docker network — or, with the Tailscale
interface present, from inside the tailnet by binding MediaMTX to the
Tailscale IP.

To expose MediaMTX on the Tailscale interface (but still not publicly), edit
`docker-compose.override.yml` to bind the ports to the Tailscale IP instead of
all interfaces:

```yaml
services:
  mediamtx:
    ports: !reset
      - "100.x.y.z:1935:1935" # replace with this host's Tailscale IPv4
      - "100.x.y.z:8554:8554"
```

Find the Tailscale IP via `tailscale ip -4`. Restart the stack with
`docker compose up -d mediamtx`. Tailscale-connected ATAK clients can then
watch via `rtsp://viewer:<MEDIAMTX_VIEWER_PASSWORD>@<tailscale-hostname>:8554/...`.

To publish over Tailscale too, set `MEDIAMTX_PUBLISH_IPS=100.64.0.0/10` in
`.env` and use the `publisher` user; publishing is off by default. See
[MediaMTX access](configuration.md#mediamtx-access).

Skip this step if you don't need video and just want SSH over Tailscale.

### SSH lockdown

Once Tailscale SSH is working, you can tighten Lightsail's firewall further
by removing public SSH (22 from your IP) entirely and relying on Tailscale
SSH. That closes the last public management port. Optional — some operators
prefer a public SSH fallback in case Tailscale is unreachable.

## 14. Troubleshooting

### Let's Encrypt cert acquisition fails

Check Caddy logs:

```bash
docker compose logs caddy | grep -i acme
```

Common causes:

- **DNS not propagated** — `dig +short tak.example.com` returns nothing or the
  wrong IP. Wait and retry.
- **Port 80 not reachable** — Lightsail firewall missing the HTTP rule, or
  Ubuntu UFW is blocking. Check with `curl -I http://tak.example.com` from
  outside.
- **Rate limiting** — Let's Encrypt limits to 50 certs per domain per week.
  If you've been iterating on deployments, you may be blocked temporarily.
  Switch to staging with `tls internal` in the Caddyfile for testing (requires
  customizing init-config) or wait.

### TAK Server won't go healthy

```bash
docker compose logs tak-server | tail -100
```

Common causes:

- **OOM kill** — the container's memory cap was exceeded. Check with
  `docker stats`. Default cap is 4 GB (DD-034); should fit in 16 GB host but
  if you see OOMs under load, adjust via `docker-compose.override.yml`.
- **Database connection** — if `tak-database` isn't healthy yet, TAK Server
  retries. Wait ~2 minutes on first boot.
- **Certificate path issues** — `init-config` produces files TAK Server
  expects. Check `docker compose logs init-config` for errors.

### ATAK enrollment fails with "connection failed: error sending request"

If ATAK reaches the network-layer handshake (`nc -vz tak.example.com 8446`
succeeds, `curl -kv https://tak.example.com:8446/` gets a 401) but ATAK still
reports "CSR enrollment failed: connection failed: error sending request"
when scanning a QR, the cause is almost always **a stale enrollment QR**.

The QR's data package bundles the TAK CA cert at QR-generation time. If
FastTAK's certs were regenerated after the QR was made (re-running
`setup.sh`, wiping `tak/certs/files/`, or any first-boot re-init), the
bundled CA no longer matches the server's current issuer. ATAK's TLS layer
rejects the connection; "connection failed" is the generic wrapper over the
real "certificate signed by unknown CA" error.

Fix:

1. In the monitor, delete the enrollment token for that user (revoke/delete
   from the Users page).
2. Generate a fresh QR via Enroll — this packages the current TAK CA.
3. On the ATAK device, delete any partial/incomplete server entry for this
   FastTAK instance before scanning the new QR.

To verify the monitor's enrollment output carries the current CA:

```bash
# CA that TAK Server presents on 8446
openssl s_client -connect localhost:8446 -showcerts </dev/null 2>/dev/null \
  | awk '/-----BEGIN CERT/,/-----END CERT/' \
  | openssl x509 -noout -issuer -fingerprint -sha256 | tail -2

# CA packaged into the monitor's enrollment QRs
openssl x509 -in tak/certs/files/ca.pem -noout -subject -fingerprint -sha256
```

Both fingerprints should match. If they don't, restart `monitor` so it
re-reads the current CA before regenerating the QR:

```bash
docker compose restart monitor
```

### Accidentally locked out of /auth/verify

DD-035's rate limit kicks in after 10 bad auth attempts per IP per 5 minutes,
with a 15-minute lockout. If you're iterating on client configuration and
got rate-limited:

```bash
docker compose restart ldap-proxy
```

This restarts the process and clears the in-memory rate-limit state. Operators
coming from IPs blocked by a previous attack won't benefit from this — it's
for yourself during setup.

### Can't SSH after enabling UFW

If you enabled Ubuntu's UFW firewall and forgot to allow port 22:

1. Go to Lightsail console → Instance → **Connect**
2. Use the browser-based terminal (works via AWS's out-of-band path, not SSH)
3. Run `sudo ufw allow 22` and re-test

## What you've built

- Public, mTLS-authenticated TAK Server on ports 8443 (enrollment) and 8089 (CoT)
- LDAP-authenticated monitor dashboard at `https://monitor.tak.example.com`
- Node-RED flow engine at `https://nodered.tak.example.com`
- Automatic Let's Encrypt TLS for all subdomains
- Rate-limited authentication (DD-035)
- Container memory caps (DD-034)
- Auto-generated admin credentials (DD-033)
- Multi-arch images (DD-036)
- (Optional) Tailscale tailnet access for administrators

Ongoing cost: **$80/mo** (instance) + optional **~$4/mo** (snapshots) + **$0**
(static IP, while attached) + **$0** (Tailscale, free tier covers ≤3 users).

## Next steps

- Set up scheduled snapshots via Lightsail console (daily, 7-day retention is a
  good starting point)
- Review the [Authentication guide](authentication.md) for user management
- See the [Node-RED guide](nodered.md) for flow development
- See the [Certificates guide](certificates.md) for cert rotation and revocation
