# Minimal compatibility fix on the pinned IDEA Open WebUI release.
FROM ghcr.io/uhsealevelcenter/idea-open-webui:0.11.0-idea.0.9@sha256:1ec23855573f3a146b8d18d70bf8248f242b202e46907ad39d3da8f9591e2a6e

# The UI and chat access checks support Open sharing, but this release's
# default-permissions request/response schema drops sharing.open_chats.
# Fail the build if the upstream schema changes; review/remove this patch
# when upgrading the base release.
RUN python -c 'from pathlib import Path; p = Path("/app/backend/open_webui/routers/users.py"); s = p.read_text(); old = "    public_chats: bool = False\n    public_calendars: bool = False"; assert s.count(old) == 1, "Review Open sharing schema patch for this release"; p.write_text(s.replace(old, "    public_chats: bool = False\n    open_chats: bool = False\n    public_calendars: bool = False"))'

# Enable Open Terminal and live sandbox FileNav in the UI layout.
# In 0.11.0-idea.0.9, IDEA_OPEN_TERMINAL_ENABLED was compiled as false,
# which stripped system terminal server loading and reset selectedTerminalId.
RUN python -c '\
from pathlib import Path; \
p = Path("/app/build/_app/immutable/nodes/2.BXke-RUS.js"); \
s = p.read_text(); \
old1 = "qd.set(null);const re=Ie().catch"; \
new1 = "qd.set(localStorage.selectedTerminalId??null);const re=Ie().catch"; \
assert s.count(old1) == 1, "Review layout selectedTerminalId patch for this release"; \
s = s.replace(old1, new1); \
old2 = "{ms.set([]),qd.set(null),localStorage.removeItem(\"selectedTerminalId\");return}"; \
new2 = "{try{const _r=await fetch(\"/api/v1/terminals/\",{headers:{Authorization:\"Bearer \"+localStorage.token}}),_d=await _r.json(),_st=_d.map(t=>({id:t.id,url:\"/api/v1/terminals/\"+t.id,name:t.name,key:localStorage.token,contexts:t.contexts||{},config:t.config||{}}));ms.set(_st);if(_st.length>0)qd.set(localStorage.selectedTerminalId||_st[0].id);}catch(e){console.error(\"Failed to load terminal servers:\",e);ms.set([]);qd.set(null);}return}"; \
assert s.count(old2) == 1, "Review layout terminalServers patch for this release"; \
s = s.replace(old2, new2); \
p.write_text(s)'

# Expose Terminal capability in Model Workspace editor so terminalId is preserved on assistant save/clone.
RUN python -c '\
from pathlib import Path; \
p = Path("/app/build/_app/immutable/chunks/DHZ8VSvu.js"); \
s = p.read_text(); \
old = "code_interpreter:{label:a().t(\"Code Interpreter\"),description:a().t(\"Assistant can execute code and perform calculations\")},"; \
new = "code_interpreter:{label:a().t(\"Code Interpreter\"),description:a().t(\"Assistant can execute code and perform calculations\")},terminal:{label:a().t(\"Terminal\"),description:a().t(\"Assistant can execute commands in an execution sandbox terminal\")},"; \
assert s.count(old) == 1, "Review Capabilities terminal patch for this release"; \
p.write_text(s.replace(old, new))'
