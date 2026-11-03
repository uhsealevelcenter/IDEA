"""Apply narrowly scoped fixes to the pinned IDEA Open WebUI backend.

Fail the image build if upstream changes any expected anchor. This keeps a
version bump from silently dropping the integration's preprocessing rules.
"""

from pathlib import Path
import sys


REPLACEMENTS = (
    (
        "    # Model \"Knowledge\" handling\n",
        """    # Use the same retrieval policy for status events and native RAG.
    capabilities = model.get('info', {}).get('meta', {}).get('capabilities') or {}
    file_context_enabled = capabilities.get('file_context', True) and not capabilities.get('raw_file_access', False)

    # Model "Knowledge" handling
""",
    ),
    (
        "    if model_knowledge and metadata.get('params', {}).get('function_calling') == 'legacy':\n        await event_emitter(\n",
        """    model_knowledge_search = bool(
        model_knowledge
        and metadata.get('params', {}).get('function_calling') == 'legacy'
        and file_context_enabled
    )
    if model_knowledge_search:
        await event_emitter(
""",
    ),
    (
        "        knowledge_files = []\n        for item in model_knowledge:\n",
        """    # Always forward legacy Knowledge descriptors, including raw-file
    # Assistants: IDEA/PaperQA resolves these with the user's authorization.
    if model_knowledge and metadata.get('params', {}).get('function_calling') == 'legacy':
        knowledge_files = []
        for item in model_knowledge:
""",
    ),
    (
        "        terminal_capability = (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('terminal', True)\n",
        """        # IDEA's internal sandbox API backs the Files explorer. It is not
        # an LLM tool server: the Pipe owns sandbox execution. Keep terminal_id
        # and the UI capability intact, but skip tool and AGENTS.md injection.
        terminal_capability = (
            (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('terminal', True)
            and terminal_id != 'idea-sandbox'
        )
""",
    ),
    (
        """    # Check if file context extraction is enabled for this model (default True)
    file_context_enabled = (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('file_context', True)

    if (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('raw_file_access', False):
        file_context_enabled = False

""",
        "    # file_context_enabled also controls the Knowledge status above.\n",
    ),
    (
        "    if model_knowledge:\n        await event_emitter(\n",
        "    if model_knowledge_search:\n        await event_emitter(\n",
    ),
)


def patch_source(source: str) -> str:
    for old, new in REPLACEMENTS:
        count = source.count(old)
        if count != 1:
            raise ValueError(f"Expected one Open WebUI patch anchor, found {count}: {old!r}")
        source = source.replace(old, new, 1)
    compile(source, "middleware.py", "exec")
    return source


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_text(patch_source(path.read_text(encoding="utf-8")), encoding="utf-8")
