# Minimal compatibility fix on the pinned IDEA Open WebUI release.
FROM ghcr.io/uhsealevelcenter/idea-open-webui:0.11.0-idea.0.8@sha256:93b8bfeff1aedd7f9c529ebe4001715b3764a9e79df10740b932605f322f8472

# The UI and chat access checks support Open sharing, but this release's
# default-permissions request/response schema drops sharing.open_chats.
# Fail the build if the upstream schema changes; review/remove this patch
# when upgrading the base release.
RUN python -c 'from pathlib import Path; p = Path("/app/backend/open_webui/routers/users.py"); s = p.read_text(); old = "    public_chats: bool = False\n    public_calendars: bool = False"; assert s.count(old) == 1, "Review Open sharing schema patch for this release"; p.write_text(s.replace(old, "    public_chats: bool = False\n    open_chats: bool = False\n    public_calendars: bool = False"))'
