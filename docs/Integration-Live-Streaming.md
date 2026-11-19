# Live streaming with n8n

The [idea-coordinator template](../integrations/n8n/idea-live-streaming.json) routes each turn to one of `welcome-assistant`, `sea`, and `mars-assistant`. The [sea-coordinator template](../integrations/n8n/sea-coordinator.json) routes to `sea`. Both coordinators use session-scoped Simple Memory and the shared IDEA system prompt as routing context. The selected specialist streams its reply through the IDEA Pipe with the integration session header and artifact links.

Render the inactive imports with the current shared prompt before importing them into n8n.

```bash
python3 integrations/n8n/render-workflows.py .local-validation/n8n-imports
```

## Run a local demo

From the repository root, use the local Compose override and the normal IDEA deployment script. The override binds the demo services to loopback and gives n8n access to IDEA on the Compose network. Sign in to `ghcr.io` with a GitHub account that can pull the pinned IDEA Open WebUI image. Integration sessions require a Docker engine with `/dev/kvm` available. Run `bash deployment/setup_env.sh` to create missing service `.env` files and set `N8N_ENCRYPTION_KEY` in the root `.env` for the n8n volume.

```bash
export COMPOSE_PROJECT_NAME=idea-local
export COMPOSE_FILE=docker-compose.yml:docker-compose.local.yml
export COMPOSE_PROFILES=n8n
```

On a fresh local volume, initialize the required datasets before starting the sandbox. Use the [shared data guide](../shared_data/README.md) if you already have a complete dataset tree.

```bash
docker compose run --rm --build -v "$PWD/shared_data/seeds:/source:ro" \
  shared-data import /source --dataset metadata --dataset benchmarks
docker compose run --rm shared-data update altimetry
KVM_DEVICE_PATH=/dev/kvm bash deployment/deploy.sh dev
```

Open n8n at `http://localhost:5678` and IDEA at `http://localhost:8080`. Import the rendered `idea-live-streaming.json` and follow the account setup below. Use `http://nginx/api` for the IDEA specialist credential so generated plots and download links use the local IDEA origin. For the coordinator, use an OpenAI credential with `http://litellm:8080/v1` and a LiteLLM virtual key with access to a tool-capable chat model such as `gpt-6-luna`. If you only need the dashboard, run `docker compose up -d n8n` after exporting the Compose variables above.

For Python-backed plots, use the research kernel image. The [kernel guide](../interpreter_kernel/README.md#building) shows how to load an image from Docker into microsandbox’s local cache.

The local Compose override serves the [embedded chat HTML](../integrations/n8n/local-chat/index.html) at `http://localhost:8080/n8n-demo/`. Publish the workflow in local n8n so the page can use its production webhook. Keep the Chat Trigger’s allowed origin set to `http://localhost:8080`.

## Start n8n behind Traefik

On the Traefik host, n8n is available through the `n8n` profile in [docker-compose.yml](../docker-compose.yml). It uses the existing Docker network `web`, Traefik entrypoint `websecure`, and certificate resolver `le`. Docker keeps n8n data in the Compose project's named `n8n` volume.

Set `N8N_ENCRYPTION_KEY` in the ignored `.env` at the repository root. Generate a key for a new n8n data volume with `openssl rand -hex 32`. Keep the same key with the volume so n8n can decrypt its credentials. The demo hostname already has an n8n service. Use its existing deployment, or identify and retain its data volume and encryption key before migrating it to this Compose file.

```bash
docker compose up -d n8n
```

Set `COMPOSE_PROFILES=n8n` in the deployment environment to include n8n in regular IDEA deployments on that host.

## Configure the IDEA integration account and sandboxes

1. In Open WebUI, confirm `ENABLE_API_KEYS=true` in `openwebui/.env`. In **Admin Panel → Users**, create a dedicated account with role `user`. Give its group `features.api_keys` and grant the account read access to the models the workflow calls. Sign in as that user, create an API key in **Settings → Account → API Keys**, and save the key in the n8n credential.
2. In **Admin Panel → Functions**, set the IDEA Pipe valves `INTEGRATION_MODEL_ENABLED=true`, `INTEGRATION_ID=n8n-sealevel-v1`, and `INTEGRATION_ACCOUNT_IDS` to the Open WebUI user ID. Restrict access to the integration-only model to that account. You can record the user ID in an ignored `.integration_account.json` under `openwebui_user_id` for operator reference. The Pipe reads the valves from Open WebUI.
3. For an existing IDEA deployment, add missing integration settings from [langgraph/.env.example](../langgraph/.env.example) and [sandbox_service/.env.example](../sandbox_service/.env.example) to the corresponding `.env` files. Keep the existing `IDEA_IDENTITY_SECRET` and internal service token. On the deployment host, pass through `/dev/kvm` by setting `KVM_DEVICE_PATH=/dev/kvm` in the root `.env` or shell environment used by Compose. The sandbox must use the microsandbox backend. Set `IDEA_INTEGRATION_VM_DISK_MIB` to match the integration VM disk in MiB and size `IDEA_INTEGRATION_MAX_RESERVED_DISK_MIB` for the number of disks you intend to retain.
4. On that deployment, set `IDEA_INTEGRATION_SESSIONS_ENABLED=true` and set a positive `IDEA_INTEGRATION_DAILY_TOKEN_BUDGET` in `langgraph/.env`. The demo uses `10000000` tokens per day. Recreate the affected IDEA services to load their env files, then call `GET /integration-sessions/readiness` on LangGraph with the internal service token as a Bearer token. Check that `enabled` is `true`, `integration_ids` contains `n8n-sealevel-v1`, and `errors` is empty.

## Set up staging

1. Complete the account and sandbox setup above. The [integration session guide](Integration-Session-Sandboxes.md#n8n-setup) describes session routing and limits.
2. Import the rendered workflow JSON into n8n. For a workflow you already maintain, use AI Agent **3.1** for the coordinator, AI Agent Tool **3** for each specialist, and **Streaming response** for the Chat Trigger. Enable streaming on the specialist nodes and turn it off on the coordinator to avoid a second reply.
3. Assign credentials to the OpenAI Chat Model nodes. The templates use `gpt-6-luna` for the coordinator. Assign the dedicated Open WebUI integration credential to the specialist models and configure its custom header as follows.

   | Credential field | Value |
   | --- | --- |
   | Base URL | `http://nginx/api` on the IDEA Docker network, or `https://<idea-host>/api` |
   | Header Name | `X-IDEA-Integration-Session` |
   | Header Value (expression) | `={{ $("When chat message received").first().json.sessionId }}` |

   If the coordinator uses another provider, assign that provider's credential to its model node. The expression must match the Chat Trigger name in the workflow.
4. In each Chat Trigger, set **Allowed Origins** to the staging website's origin. Publish the workflows and verify that the production webhook IDs match the IDs in the HTML and nginx configuration. Allowed origins control browser access, and the integration session ID identifies a conversation. Restrict visitor access separately when the deployment requires it.

### next-dev dashboard and portals

The [next-dev Compose file](../integrations/n8n/next-dev/docker-compose.yml) runs a separate n8n project on `idea_default`. It reuses the IDEA nginx image and serves the editor at `/n8n/`, the multi-assistant chat at `/IDEA-portal`, and the SEA chat at `/n8n/SEA-portal`.

In the private server env, set `N8N_PUBLIC_HOST` to the staging host, `N8N_PUBLIC_PORT` to `15678`, and provide `N8N_INSTANCE_OWNER_EMAIL`, the owner's single-quoted bcrypt hash in `N8N_INSTANCE_OWNER_PASSWORD_HASH`, and `N8N_ENCRYPTION_KEY`. Reuse the existing key when using the existing `idea-next-dev-n8n_n8n` volume. The listener defaults to loopback. The current public staging deployment sets `N8N_BIND_ADDRESS=0.0.0.0`. The loopback listener on server port `5678` supports admin sign-in via SSH.

```bash
docker compose --env-file .local-validation/next-dev-n8n/.env \
  -f integrations/n8n/next-dev/docker-compose.yml up -d
```

For plots and downloads, mount a host mapping into the main IDEA nginx container under `/etc/nginx/local-proxy-hosts.d/`. On next-dev the mapping is `nginx 149.165.172.178:15678;`. IDEA uses it to build browser-facing links from the specialist's internal requests. The n8n gateway proxies `/integration-exports/` back to IDEA. Validate and reload IDEA nginx after changing the mapping.

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@n8n/chat@1.25.0/dist/style.css" />
<script type="module">
  import { createChat } from 'https://cdn.jsdelivr.net/npm/@n8n/chat@1.25.0/dist/chat.bundle.es.js';

  createChat({
    webhookUrl: 'https://<n8n-host>/<chat-trigger-production-path>',
    enableStreaming: true,
  });
</script>
```

## Verify live delivery

Mock tests verified early specialist chunks and distinct visitor session IDs on n8n 2.26.8. On n8n 2.48.5, the three-specialist workflow also passed mock routing and coordinator memory checks, and the local stack delivered a live Welcome response and a Python-generated plot through the embedded chat webhook. Use the same checks on staging to verify the deployed Open WebUI, proxy, and sandbox together.

Ask for a sea-level plot and a CSV download. In the browser's network inspector or an unbuffered HTTP client, look for a nonempty `item` with `metadata.nodeName` equal to `sea`. Compare its arrival time with the IDEA run's terminal status on staging. A specialist `end` frame confirms n8n finished processing the specialist response, which may be later than the IDEA run itself. Confirm that the text is readable in the widget, the export links work, and a follow-up message uses the same session. Two independent browser sessions should have different IDEA sandboxes.

If the widget shows a second answer, check whether the coordinator has streaming enabled. If the specialist sends output late, check its node version and streaming setting, then compare the webhook with a direct server-side `/api/chat/completions` request to the IDEA Assistant using `stream: true` and the integration header. If the direct endpoint emits early but the webhook sends everything at once, check the workflow path and buffering on the n8n reverse proxy.

## Streaming limits and possible solutions

| Current behavior | Possible solution |
| --- | --- |
| IDEA text streams when available. Long tool calls may be quiet because structured IDEA progress uses Open WebUI's event channel. | Relay selected IDEA run status events through an authorized endpoint if visitors need live progress during those gaps. |
| The coordinator's Simple Memory lives in the n8n process. | Connect a persistent memory node if follow-up history must survive n8n restarts. |
| Plot previews and download links arrive once IDEA publishes the turn's exports. | Publish each artifact when ready if the site needs plots during execution. |
| IDEA can keep running after the browser disconnects. The widget has no run-event replay. | Provide authorized Stop and resume operations using IDEA's run ID and event sequence. |
| The stock HTTP Request → Respond to Webhook path waits for the upstream SSE body to finish. | Keep IDEA on the streaming Chat Model path for text, or relay IDEA run events from an IDEA-owned endpoint for richer output. |
