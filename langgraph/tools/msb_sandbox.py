"""
MOVED to /sandbox_service/msb_sandbox.py as part of the sandbox_service
microservice split (see /langgraph/IMPLEMENTATION_STATUS.md).

The langgraph service no longer talks to microsandbox/pexpect directly -
tools/persistent_terminal.py is now a thin HTTP client to sandbox_service,
which owns this logic. This file is kept only as a pointer; it is not
imported anywhere in this service.
"""

