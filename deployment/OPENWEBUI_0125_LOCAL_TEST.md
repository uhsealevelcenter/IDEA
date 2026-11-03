# Open WebUI 0.12.5 preprocessing fixes

The wrapper uses the pinned `0.12.5-idea.0.11` image and applies
`openwebui/patch_preprocessing.py` to its backend middleware. No Assistant
reconciliation or database migration is needed for these fixes.

## Findings

In `process_chat_payload`, legacy model Knowledge handling emits
`knowledge_search` before copying Knowledge descriptors into `form_data.files`.
That block performs no retrieval. Later, native retrieval through
`chat_completion_files_handler` is skipped when `file_context` is false or
`raw_file_access` is true. The Welcome Assistant has both settings, so the
preprocessing status does not mean a Knowledge search occurred. IDEA/PaperQA
may independently search literature during the agent run.

The patch uses the retrieval policy for the start and completion status events,
while preserving legacy collection normalization and forwarding. Keep legacy
function calling, Knowledge attachments, and PaperQA configuration intact.

The selected `terminal_id` is separately resolved by `get_terminal_tools` into
OpenAPI-backed callables. In legacy mode, `chat_completion_tools_handler`
advertises those operations to the Task model and can execute them before the
IDEA Pipe runs. The handler formats a nonempty tool result as a source named
`{tool_id}/{operation}`, explaining labels such as
`terminal:idea-sandbox/run_python_sandboxes__sandbox_id__run_python_post`.
This label represents a tool result, not a literature source.

The patch excludes the internal `idea-sandbox` connection from terminal tool
resolution, terminal prompt injection, and the terminal `AGENTS.md` path.
It retains the selected terminal ID, saved connection, terminal capability,
and browser/proxy file APIs used by Files. It does not suppress citations from
other tools or change IDEA/PaperQA execution. The rule is intentionally tied
to the internal connection ID configured by `configure_openwebui.py`; a renamed
or separately registered sandbox connection would need the same exclusion.

## Automated checks

Build an isolated image without restarting the running stack:

```bash
docker build -t idea-openwebui:0125-preprocessing-test openwebui
```

The build fails if patch anchors change. It then executes ten behavioral tests
against the patched image's actual preprocessing statements with mocked I/O:
raw-file and disabled-context requests skip native search and status;
collection descriptors survive; ordinary legacy retrieval still reports status;
native handling emits no legacy status; internal sandbox tools never reach the
Task handler; other terminals and selected user tools remain available.

Run the existing integration regressions from the repository root:

```bash
.venv/bin/python -m unittest discover -s tests -p test_idea_pipe_assistants.py
.venv/bin/python -m unittest discover -s tests -p test_paperqa_openwebui_library.py
.venv/bin/python -m unittest discover -s tests -p test_deploy_assistants_openwebui.py
.venv/bin/python -m unittest discover -s tests -p test_configure_openwebui.py
```

For a local middleware copy, apply the patch once, then run the same behavioral
checks used by the image build:

```bash
python3 openwebui/patch_preprocessing.py /tmp/middleware.py
OPENWEBUI_MIDDLEWARE_PATH=/tmp/middleware.py python3 openwebui/test_preprocessing.py
```

## Browser checks after deploying the rebuilt wrapper

1. Start a new Welcome Assistant chat with an attached Knowledge collection.
   Send a greeting. Expect IDEA's progress updates without Open WebUI's
   preliminary “Searching Knowledge” status or an internal terminal source.
2. Ask a literature question requiring the attached collection. Confirm
   PaperQA resolves authorized collection PDFs and returns literature citations.
3. Open Files, list the sandbox files, upload a file, and preview/download it.
   Ask IDEA to analyze the file and create an output; confirm the output is
   accessible from Files and the response.
4. Confirm a normal Assistant with native file context enabled still retrieves
   Knowledge, and a separately configured external terminal still offers tools.
5. Inspect new responses for `terminal:idea-sandbox/...` sources. Existing
   source labels in saved messages are not rewritten by this patch.

The automated checks exercise backend preprocessing and existing Pipe/PaperQA
contracts. Browser interactions and a live model-backed PaperQA run require
the rebuilt wrapper to be deployed and are not covered by those checks.

## Local validation on November 2, 2026

- Built `idea-openwebui:0125-preprocessing-test` successfully from the pinned
  image; all ten in-image preprocessing checks passed.
- Passed 94 existing tests: 50 Pipe, 9 PaperQA library, 21 Assistant deployment,
  and 14 Open WebUI configuration checks.
- Confirmed the patch rejects missing anchors and already-patched source.
- Left the running containers unchanged. The browser checks above and live
  PaperQA retrieval have not been run against the rebuilt wrapper.
