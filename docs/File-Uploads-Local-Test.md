# Local file upload testing

Local testing passed, as reported by the user on 2026-09-06, after building
with the 4 GB heap and reconciling the official assistant capabilities.

The `fix/file-uploads` branches in IDEA and IDEA-open-webui work together.
IDEA advertises `raw_file_access` on its official assistants and base model.
The customized frontend stores chat attachments with `process=false` and returns
as soon as storage succeeds, without requesting a processing-status stream.
The backend also skips native RAG context for raw-file models. PaperQA lazily
synchronizes PDF, DOCX, DOC, ODT, and RTF literature documents when
`query_knowledge_base` is first invoked. Office documents are converted to PDF
inside LangGraph before PaperQA indexes them. Knowledge-library uploads still
use their existing processing path.

## Build and start locally

These commands assume the checkouts are at `dev/IDEA-next/IDEA` and
`dev/IDEA-open-webui`. Run from the IDEA directory. The override reuses the
existing Open WebUI data volume and preserves the Open Sharing patch.

Use a 4 GB Node heap for the local frontend build; this succeeded on the user's
8 GB environment. The release workflow uses 8 GB, which left too little memory
for other processes locally. The heap limit is not a total process-memory cap.

```bash
# Build the existing backend with its Open Sharing patch.
docker compose build openwebui

# Build the updated frontend in the sibling checkout.
(cd ../../IDEA-open-webui && npm ci --force && NODE_OPTIONS=--max-old-space-size=4096 npm run build)

# Start the agent and its dependencies, including the normal local dev override.
docker compose up -d langgraph

# Add the updated frontend and middleware to the existing backend.
docker compose -f docker-compose.yml -f docker-compose.file-uploads-local.yml build openwebui
docker compose -f docker-compose.yml -f docker-compose.file-uploads-local.yml up -d --no-deps openwebui

# Wait until this succeeds before deploying assistant settings.
curl --fail http://localhost:3001/health

# Reconcile official assistant and IDEA Agent capabilities using the existing setup.
./assistants/deploy_assistants_openwebui.py --reconcile
```

`--no-deps` above replaces only Open WebUI; it does not start LangGraph. Check
`docker compose ps` if chat reports that the `langgraph` hostname cannot be
resolved. `docker compose up -d langgraph` starts its declared dependencies
without replacing the running customized Open WebUI image. On a fresh stack,
complete the normal database/service setup described in the README as well.

Reload the browser after deployment. For a separately maintained custom IDEA
assistant such as CIndRA, enable **Raw File Access** under its model capabilities.
The deployment script intentionally preserves custom assistants' settings.
Enable this capability only on integrations that can download original files.

`--reconcile` is required for existing official assistants: without it the
script skips their settings and they will not receive `raw_file_access`.
Reconciliation restores repository-managed fields, including prompts, on those
assistants. Afterward, reload the browser and upload a fresh file; it does not
cancel processing already started by an earlier upload.

## Short manual check

1. Select an official IDEA assistant. Drag in a multi-megabyte CSV and one MAT
   or NetCDF file. Repeat one upload through the file picker. The attachment
   should become ready when the upload POST finishes. In browser Network tools,
   confirm `POST /api/v1/files/?process=false` and no processing-status request.
2. Ask IDEA to list the uploaded files, report their sizes and SHA-256 hashes,
   and read a few values with the appropriate library. Compare one hash with
   local `sha256sum`. Check Open WebUI logs for unexpected extraction or
   sentence embedding during upload.
3. Attach a PDF and a Word document and ask questions using
   `query_knowledge_base`. Then query an existing mixed PDF/Word Knowledge
   collection. All supported documents should return PaperQA answers/citations;
   unsupported or failed documents should appear as explicit warnings. PaperQA
   may still perform its own indexing when queried; raw upload does not disable
   that behavior.
4. Check an image and an upload failure (for example, a file above the configured
   size limit). A failed upload should show an error and remove its pending card.
5. If generic models are available, select one and confirm its upload still
   processes. Mixed selections also process unless every model opts into raw
   access. Raw files reused from storage have no native RAG index: re-upload
   with the generic model selected when native RAG is needed.

Temporary IDEA chats also need server-stored originals for sandbox access;
their raw attachments use the normal authenticated file storage path. This
change does not add a new file-retention policy. Existing upload size and
authorization checks remain in force. Open WebUI's RAG extension allowlist is
processing-specific and, as with its existing raw-upload API, does not apply
to `process=false` uploads.

## Published integration

The tested customization is published as `v0.11.0-idea.0.9`. The normal IDEA
wrapper pins its immutable GHCR digest and retains the Open Sharing patch. The
local override remains available for testing uncommitted Open WebUI changes;
routine testing should use the normal pinned image.

To return to the current pinned image locally:

```bash
docker compose up -d --no-deps openwebui
```
