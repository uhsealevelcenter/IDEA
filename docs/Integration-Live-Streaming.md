# Live streaming with n8n

The [IDEA live streaming workflow](../integrations/n8n/idea-live-streaming.json) pairs the integration sessions from [PR #96](https://github.com/uhsealevelcenter/IDEA/pull/96) with a v3.1 n8n coordinator and a v3 IDEA Agent Tool. These node versions let the specialist emit its own text to the embedded chat while it works. The v2.2 nodes can wait for the specialist to finish and then stream the coordinator's answer. The workflow uses the existing IDEA Pipe, session header, and export endpoints.

## Set up staging

1. Configure the IDEA integration account and sandbox settings in the [integration session guide](Integration-Session-Sandboxes.md#n8n-setup).
2. Import the workflow JSON into n8n. It is an inactive reference workflow with one sea-level specialist. For an existing workflow, copy it in n8n and use AI Agent **3.1** for the coordinator and AI Agent Tool **3** for each IDEA specialist. Keep the existing routes, prompts, and Chat Trigger name. Enable streaming on the agent nodes and set the Chat Trigger response mode to **Streaming response**.
3. Assign the OpenAI-compatible credentials in the two model nodes. The template uses the demo model IDs `coordinator` and `sea`. Select the corresponding tool-capable coordinator and IDEA Assistant on your deployment. Use the dedicated Open WebUI integration credential on any model node that calls IDEA. Keep the API key in n8n and set its custom header as follows.

   | Credential field | Value |
   | --- | --- |
   | Base URL | `https://<idea-host>/api` |
   | Header Name | `X-IDEA-Integration-Session` |
   | Header Value (expression) | `={{ $("When chat message received").first().json.sessionId }}` |

   If the coordinator uses another provider, assign that provider's credential to its model node. The expression must match the Chat Trigger name in the workflow.
4. In the Chat Trigger, set **Allowed Origins** to the staging website's origin. The import defaults to `http://localhost:8080` for local testing. Publish on staging and use the **Production URL** shown by the Chat Trigger in the widget configuration. Keep the staging webhook behind your staging access controls. The allowed-origin setting controls browser access and the integration session ID identifies the conversation. Visitor authorization requires its own check.

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

With n8n 2.26.8 and a local mock IDEA endpoint, this export delivered two separate specialist text chunks before the specialist ended and forwarded distinct session IDs for two visitors. Use the staging check below to verify the deployed Open WebUI, proxy, and sandbox together.

Ask for a sea-level plot and a CSV download. In the browser's network inspector or an unbuffered HTTP client, look for a nonempty `item` with `metadata.nodeName` equal to `IDEA Specialist`. Compare its arrival time with the IDEA run's terminal status on staging. A specialist `end` frame confirms n8n finished processing the specialist response, which may be later than the IDEA run itself. Confirm that the text is readable in the widget, the export links work, and a follow-up message uses the same session. Two independent browser sessions should have different IDEA sandboxes.

If the widget shows only the coordinator's answer, check the specialist's node version and streaming setting first. Then compare the webhook with a direct server-side `/api/chat/completions` request to the IDEA Assistant using `stream: true` and the integration header. That shows whether the text is delayed upstream of n8n. If the direct endpoint emits early but the webhook sends everything at once, check the workflow path and buffering on the n8n reverse proxy.

## Streaming limits and possible solutions

| Current behavior | Possible solution |
| --- | --- |
| IDEA text streams when available. Long tool calls may be quiet because structured IDEA progress uses Open WebUI's event channel. | Relay selected IDEA run status events through an authorized endpoint if visitors need live progress during those gaps. |
| The widget can display the specialist and coordinator as separate messages. | Keep the coordinator's final reply brief, as in the template. Use a custom widget if both streams need to appear in a single reply. |
| Plot previews and download links arrive once IDEA publishes the turn's exports. | Publish each artifact when ready if the site needs plots during execution. |
| IDEA can keep running after the browser disconnects. The widget has no run-event replay. | Provide authorized Stop and resume operations using IDEA's run ID and event sequence. |
| The stock HTTP Request → Respond to Webhook path waits for the upstream SSE body to finish. | Keep IDEA on the streaming Chat Model path for text, or relay IDEA run events from an IDEA-owned endpoint for richer output. |
