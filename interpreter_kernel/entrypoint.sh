#!/bin/bash
# Combined entrypoint for this image: starts Open Terminal (unmodified,
# via its own entrypoint-slim.sh - handles home dir setup and the optional
# egress firewall) AND our persistent-kernel daemon (daemon.py) as two
# processes in the same container/VM. Both only ever bind to 127.0.0.1
# (Open Terminal on 8000, the kernel daemon on 8721) - neither is reachable
# from outside this VM; sandbox_service reaches both indirectly via the
# microsandbox SDK's own sandbox.shell(), which runs commands (curl, or
# client.py) *inside* the VM. See sandbox_service/msb_sandbox.py.
set -e

# Open Terminal requires an API key (see verify_api_key in open_terminal/
# main.py) and auto-generates one if unset, but only logs it - not
# reliably scrapeable by sandbox_service. Instead, generate it ourselves
# and drop it at a fixed path sandbox_service reads once via the SDK's
# fs.read() right after this VM first boots (see msb_sandbox.py's
# _get_open_terminal_key) - this never leaves the VM as a network request,
# only as a file read through the same channel already used for file I/O.
KEY_PATH="/opt/oi_kernel/.open_terminal_api_key"
if [ ! -f "$KEY_PATH" ]; then
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEY_PATH"
fi
export OPEN_TERMINAL_API_KEY
OPEN_TERMINAL_API_KEY="$(cat "$KEY_PATH")"
chmod 644 "$KEY_PATH"

# Open Terminal's own entrypoint ends in `exec`, which is fine backgrounded
# here - it just replaces this background job's process, not our script.
/app/entrypoint-slim.sh run &
OT_PID=$!

# Give fix_home/the firewall setup a moment; our daemon only needs
# /home/user and gosu to exist, not Open Terminal to be serving yet.
sleep 1
gosu user /opt/idea-venv/bin/python /opt/oi_kernel/daemon.py &
KERNEL_PID=$!

# If either process dies, stop the other rather than limping along with
# half the VM's capabilities silently unavailable.
wait -n "$OT_PID" "$KERNEL_PID"
EXIT_CODE=$?
echo "One of Open Terminal / the OI kernel daemon exited (code $EXIT_CODE) - stopping the other."
kill "$OT_PID" "$KERNEL_PID" 2>/dev/null || true
exit "$EXIT_CODE"
