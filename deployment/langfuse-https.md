# Production Langfuse HTTPS

Public URL: https://langfuse.ideaxiom.org. Production host: `idea-prod`
(`149.165.150.102`), repository `/home/exouser/IDEA`, Compose project `idea`.

## DNS and routing

Create a Cloudflare DNS-only A record named `langfuse` pointing to
`149.165.150.102`. Remove conflicting AAAA records unless IPv6 is configured.
DNS changes require separate authorization. Check resolution before issuance:

```bash
getent ahostsv4 langfuse.ideaxiom.org
```

Nginx's existing production file `nginx/nginx-https-prod.conf` contains separate
virtual hosts and certificates for IDEA and Langfuse. Langfuse HTTP traffic
redirects to HTTPS, except `/.well-known/acme-challenge/`, served from
`certbot/www`. HTTPS forwards the full URI to `http://idea_langfuse:3000` on
`idea_default`, with Host, real IP, forwarded IP, protocol, and host headers.
Variable-based `proxy_pass` uses the existing Docker DNS resolver so container
IP changes do not require an Nginx restart. IDEA routing remains unchanged.

## Configuration

Installed and validated version: Langfuse **2.95.11**. This change reuses the
existing image; it does not pull or build a newer version.

- `langfuse/.env`: `NEXTAUTH_URL=https://langfuse.ideaxiom.org`.
- `deployment/config.yaml`, `prod`: `LANGFUSE_PUBLIC_URL` sets that origin;
  `deploy.sh` runs `set_langfuse_url.py` to preserve it on future deployments.
- The updater changes only `NEXTAUTH_URL`; database URL, authentication secret,
  salt, encryption key, bootstrap credentials and API keys stay intact.
- Production `LANGFUSE_HOST_PORT=127.0.0.1:3050:3000` permits host diagnostics;
  Nginx reaches port 3000 directly over the Docker network.
- LiteLLM retains `LANGFUSE_HOST=http://langfuse:3000`. Existing internal SDK
  URLs and keys do not need changes. External clients should use the HTTPS URL.

For this root-hostname deployment, v2 needs no base path, custom image, extra
public URL, cookie or CORS setting. HTTPS enables secure NextAuth cookies.
Only email/password authentication is currently configured. If SSO is added,
register `https://langfuse.ideaxiom.org/api/auth/callback/<provider>` with the
provider. See the [official v2 deployment guide](https://langfuse.com/self-hosting/v2/deployment-guide).

## Back up before changes

Run as `exouser` from the repository. Use a private, ignored directory because
service environment files contain secrets:

```bash
cd /home/exouser/IDEA
umask 077
backup_dir="$PWD/data/operations/langfuse-https-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
cp --parents docker-compose.yml nginx/nginx.conf nginx/nginx-https-prod.conf \
  deployment/config.yaml deployment/deploy.sh deployment/renew-production-cert.sh \
  langfuse/.env "$backup_dir/"
crontab -l > "$backup_dir/crontab.txt"
```

Keep the normal database backup schedule. Never regenerate secrets, reset the
database, remove volumes, or run `docker compose down -v` for this change.

## Initial certificate issuance: do this before activating the new virtual host

The existing port-80 server already serves HTTP-01. With the previous production
Nginx configuration still running, issue a **separate** lineage using the existing
ACME account and mounted webroot. Do not replace or expand the IDEA certificate.
For a fresh installation, first bootstrap the existing HTTP-only configuration
and an ACME account; the command below assumes the existing production account.

```bash
set -euo pipefail
cd /home/exouser/IDEA
docker run --rm \
  -v "$PWD/certbot/conf:/etc/letsencrypt" \
  -v "$PWD/certbot/www:/var/www/certbot" \
  certbot/certbot:latest certonly --webroot --webroot-path /var/www/certbot \
  --cert-name langfuse.ideaxiom.org -d langfuse.ideaxiom.org \
  --non-interactive --keep-until-expiring
```

Only after successful issuance, install the updated production Nginx file.
It references `/etc/letsencrypt/live/langfuse.ideaxiom.org/{fullchain,privkey}.pem`.
For existing single-file Docker bind mounts, overwrite file contents in place;
an atomic rename can leave the container reading the previous inode.

## Targeted rollout

Do not run the full-stack `deploy.sh` solely for this transition. Validate the
candidate Nginx configuration before reloading, then wait for HTTPS readiness:

```bash
set -euo pipefail
cd /home/exouser/IDEA
docker compose -p idea exec -T nginx nginx -t
docker compose -p idea exec -T nginx nginx -s reload
curl --fail --silent --show-error --retry 10 --retry-delay 2 --retry-all-errors \
  -o /dev/null https://langfuse.ideaxiom.org
```

Inspect consumers of port 3050 before closing it. Once the hostname serves HTTPS,
apply the canonical URL and loopback binding, recreating only Langfuse:

```bash
set -euo pipefail
cd /home/exouser/IDEA
eval "$(python3 deployment/load_env.py prod)"
python3 deployment/set_langfuse_url.py "$LANGFUSE_PUBLIC_URL"
docker compose -p idea config --quiet
docker compose -p idea up -d --no-deps --no-build --pull never langfuse
curl --fail --silent --show-error --retry 20 --retry-delay 2 --retry-all-errors \
  https://langfuse.ideaxiom.org/api/public/health
```

A short Langfuse startup interruption is expected; IDEA containers keep running.
Users must log in on the new hostname; cookies from the old IP do not transfer.

## Renewal

`deployment/renew-production-cert.sh` renews **all** saved certificate lineages,
then tests Nginx and reloads it only after successful renewal checks. Certbot uses
the persisted webroot settings. Both certificate mounts must remain available.
The production user's existing renewal cron entry should be:

```cron
17 3 * * * /bin/bash /home/exouser/IDEA/deployment/renew-production-cert.sh >> /home/exouser/idea-cert-renewal.log 2>&1
```

This runs at 03:17 in the cron host's timezone. Preserve other cron jobs when
correcting a stale path. Test both lineages against Let's Encrypt staging:

```bash
bash deployment/renew-production-cert.sh --dry-run --no-random-sleep-on-renew
```

## Validation

- `curl -I https://langfuse.ideaxiom.org` must validate TLS without `-k`.
- `curl -I 'http://langfuse.ideaxiom.org/test?check=1'` must redirect to the same
  HTTPS path and query. An HTTP-01 test file must remain accessible over HTTP.
- `/api/public/health` must report OK; `/api/auth/providers` must advertise the
  HTTPS origin. Log in with an existing user, confirm project/history access,
  then log out and verify the session is cleared. Session cookies must be Secure.
- With existing project API keys, GET `/api/public/projects` and
  `/api/public/traces?limit=1` using HTTP Basic authentication. Keep keys out of
  shell history, logs and command arguments.
- Authenticate and flush a labeled validation trace with the installed SDK in
  the LiteLLM container, then retrieve that trace through the public HTTPS API.
- Compare project, user and trace counts with the baseline (allowing new traces);
  verify old records remain, the image/secrets are unchanged, and unrelated
  container IDs and start times are unchanged.
- `curl --fail https://app.ideaxiom.org -o /dev/null` and `nginx -t` must pass.
- Check `docker port idea_langfuse`: only `127.0.0.1:3050` should be published.
  Confirm the public IP's port 3050 cannot be reached, ideally from another host.

## Rollback

Use the backup path printed during rollout. Restore the original production
Nginx file **in place**, test it, and reload. Keep both certificates on disk:

```bash
set -euo pipefail
cd /home/exouser/IDEA
# Set backup_dir to the actual pre-change backup directory.
cat "$backup_dir/nginx/nginx-https-prod.conf" > nginx/nginx-https-prod.conf
docker compose -p idea exec -T nginx nginx -t
docker compose -p idea exec -T nginx nginx -s reload
cat "$backup_dir/langfuse/.env" > langfuse/.env
cat "$backup_dir/deployment/config.yaml" > deployment/config.yaml
eval "$(python3 deployment/load_env.py prod)"
docker compose -p idea up -d --no-deps --no-build --pull never langfuse
```

Restoring the previous production config reopens the old public port 3050;
use it only if that fallback is needed. Keep the corrected renewal cron path.
Do not run a full deployment during rollback; remove the new production URL
setting from tracked deployment configuration before future full deployments.
No data restoration or volume replacement is needed.

## Production rollout results — October 30, 2026 (Hawaii time)

Deployed on `idea-prod`. Configuration and cron backups are in
`/home/exouser/IDEA/data/operations/langfuse-https-20261031T001713Z`
(the directory timestamp is UTC). It is private and ignored by Git.

Verified:

- Public DNS resolved to `149.165.150.102`; HTTPS UI and health returned 200.
- TLS hostname validation passed. The separate Langfuse certificate expires
  January 28, 2027. IDEA's certificate remains in its separate lineage.
- HTTP redirected with the path/query intact; an HTTP-01 test file was served
  directly and then removed.
- Existing-user credentials authenticated successfully, established a Secure
  session cookie, and logout cleared the authenticated session.
- Authenticated project and trace APIs returned existing data over HTTPS.
- The original project, user and 308 traces remained. One labeled SDK validation
  trace was added from LiteLLM using `http://langfuse:3000`, flushed, and retrieved
  through HTTPS: `4f741795-9b57-4a10-b487-c7f06c746df9`.
- Langfuse retained its exact image and every environment value except
  `NEXTAUTH_URL`. Unrelated container IDs and start times were unchanged.
- Only `127.0.0.1:3050` is published. Connecting to the public IP on port 3050
  failed from the production host; a separate off-host probe was not performed.
- IDEA returned 200, Nginx configuration passed, and renewal dry runs for both
  saved certificate lineages completed successfully. The stale cron path was
  corrected, preserving the database backup job.
- Shell syntax, Python compilation, Compose configuration, Git whitespace checks,
  and URL-updater idempotence passed. Login/UI checks used HTTP requests rather
  than an interactive browser.
