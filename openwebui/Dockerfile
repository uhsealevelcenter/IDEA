# Minimal compatibility fix on the pinned IDEA Open WebUI release.
FROM ghcr.io/uhsealevelcenter/idea-open-webui:0.11.0-idea.0.9@sha256:1ec23855573f3a146b8d18d70bf8248f242b202e46907ad39d3da8f9591e2a6e

# The UI and chat access checks support Open sharing, but this release's
# default-permissions request/response schema drops sharing.open_chats.
# Fail the build if the upstream schema changes; review/remove this patch
# when upgrading the base release.
RUN python -c 'from pathlib import Path; p = Path("/app/backend/open_webui/routers/users.py"); s = p.read_text(); old = "    public_chats: bool = False\n    public_calendars: bool = False"; assert s.count(old) == 1, "Review Open sharing schema patch for this release"; p.write_text(s.replace(old, "    public_chats: bool = False\n    open_chats: bool = False\n    public_calendars: bool = False"))'
