"""Apply IDEA's configured terminal default when a Workspace assistant is saved."""

import sys
from pathlib import Path


ANCHOR = """    await _check_model_controls(form_data, None, user, request)
    model = await Models.insert_new_model(form_data, user.id, db=db)
"""
REPLACEMENT = """    # The Workspace editor sends no terminal selection for a new assistant.
    capabilities = form_data.meta.capabilities or {}
    if (
        form_data.base_model_id
        and 'terminalId' not in form_data.meta.model_fields_set
        and capabilities.get('terminal') is not False
    ):
        metadata = await Config.get('models.default_metadata') or {}
        terminal_id = metadata.get('terminalId')
        if terminal_id:
            from open_webui.routers.terminals import list_terminal_servers

            connections = await list_terminal_servers(request, user=user)
            if any(connection['id'] == terminal_id for connection in connections):
                form_data.meta.terminalId = terminal_id
                form_data.meta.capabilities = {**capabilities, 'terminal': True}

""" + ANCHOR


if __name__ == "__main__":
    path = Path(sys.argv[1])
    source = path.read_text()
    if source.count(ANCHOR) != 1:
        raise SystemExit("Open WebUI assistant creation hook changed")
    path.write_text(source.replace(ANCHOR, REPLACEMENT))
