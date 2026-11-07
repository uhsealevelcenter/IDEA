# Production LiteLLM HTTPS

Public API/docs: https://litellm.ideaxiom.org/  
Admin UI: https://litellm.ideaxiom.org/ui/

Cloudflare's DNS-only A record points to `149.165.150.102`. The production
configuration in `nginx/nginx-https-prod.conf` redirects HTTP to HTTPS, except
for the Let's Encrypt HTTP-01 webroot. HTTPS forwards the complete request URI
to `http://idea_litellm:8080` on the Compose network. Response buffering is
disabled for streaming API responses. Forwarded host and protocol headers
preserve the public origin.

The separate certificate lineage is `litellm.ideaxiom.org`, stored under
`certbot/conf/live/`. The existing daily cron job runs
`deployment/renew-production-cert.sh`, which renews all saved lineages and
validates and reloads Nginx. Test this certificate's renewal with:

```bash
docker run --rm \
  -v "$PWD/certbot/conf:/etc/letsencrypt" \
  -v "$PWD/certbot/www:/var/www/certbot" \
  certbot/certbot:latest renew --cert-name litellm.ideaxiom.org \
  --dry-run --no-random-sleep-on-renew --non-interactive
```

After editing the bind-mounted configuration in place:

```bash
docker compose -p idea exec -T nginx nginx -t
docker compose -p idea exec -T nginx nginx -s reload
curl --fail --silent --show-error https://litellm.ideaxiom.org/health/liveliness
```

Validation: HTTPS homepage, UI, sampled UI assets, and liveliness return 200;
`/v1/models` without credentials returns 401. HTTP redirects preserve paths
and query strings. Existing app and Langfuse HTTPS endpoints remain available.
An interactive sign-in and authenticated model request require separate testing.

Initial production certificate issuance succeeded. The subsequent renewal dry
run could not complete because the Let's Encrypt staging service returned
`rateLimited: Service busy; retry later`. Automatic renewal is scheduled, but
repeat the dry-run command above when staging is available.

Direct public HTTP access on port 8030 remains enabled as previously requested.
API clients can now use `https://litellm.ideaxiom.org` as their proxy origin
(or append `/v1` when their SDK expects an OpenAI-compatible base URL).
