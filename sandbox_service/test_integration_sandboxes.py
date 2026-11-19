import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import pexpect  # noqa: F401
except ModuleNotFoundError:
    pexpect_stub = types.ModuleType("pexpect")
    pexpect_stub.TIMEOUT = TimeoutError
    pexpect_stub.EOF = EOFError
    pexpect_stub.spawn = Mock()
    sys.modules["pexpect"] = pexpect_stub

import terminal_registry as registry

SANDBOX = "sb_n8n_" + "a" * 40


class FakeReservations:
    def __init__(self, keys=()):
        self.keys = set(keys)

    def get(self, key):
        return "{}" if key in self.keys else None

    def delete(self, key):
        self.keys.discard(key)


class IntegrationSandboxTests(unittest.TestCase):
    def setUp(self):
        self.reservations = FakeReservations()
        for patcher in (
            patch.object(registry, "_reservation_client", return_value=self.reservations),
            patch.object(registry, "INTEGRATION_REQUIRE_RESERVATION", True),
            patch.dict(registry._terminals, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_local_backend_cannot_satisfy_isolation(self):
        self.reservations.keys.add(f"idea:isess:vm:{SANDBOX}")
        with (
            patch.object(registry, "_use_microsandbox", return_value=False),
            patch.object(registry, "PersistentTerminal") as local_terminal,
        ):
            with self.assertRaises(registry.SandboxAdmissionError) as raised:
                registry._get_terminal(SANDBOX)
        self.assertEqual(raised.exception.code, "isolation_unavailable")
        local_terminal.assert_not_called()

    def test_boot_requires_a_lifecycle_reservation(self):
        with (
            patch.object(registry, "_use_microsandbox", return_value=True),
            patch.object(registry, "MicrosandboxTerminal") as terminal_class,
        ):
            with self.assertRaises(registry.SandboxAdmissionError) as raised:
                registry._get_terminal(SANDBOX)
            self.assertEqual(raised.exception.status_code, 409)
            terminal_class.assert_not_called()

            self.reservations.keys.add(f"idea:isess:vm:{SANDBOX}")
            registry._get_terminal(SANDBOX)

        kwargs = terminal_class.call_args.kwargs
        self.assertEqual(terminal_class.call_args.args, (SANDBOX,))
        self.assertEqual(kwargs["labels"], registry.INTEGRATION_LABELS)
        self.assertEqual(kwargs["disk_mb"], registry.INTEGRATION_DISK_MB)

    def test_host_capacity_is_enforced_for_integration_boots(self):
        self.reservations.keys.add(f"idea:isess:vm:{SANDBOX}")
        registry._terminals["regular-user"] = Mock()
        with (
            patch.object(registry, "_use_microsandbox", return_value=True),
            patch.object(registry, "HOST_MAX_RESIDENT_VMS", 2),
            patch.object(registry, "HOST_RESERVED_REGULAR_VMS", 1),
            patch.object(registry, "MicrosandboxTerminal") as terminal_class,
        ):
            with self.assertRaises(registry.SandboxAdmissionError) as raised:
                registry._get_terminal(SANDBOX)
        self.assertEqual(raised.exception.code, "host_capacity")
        terminal_class.assert_not_called()

    def test_regular_user_sandboxes_are_unaffected(self):
        with (
            patch.object(registry, "_use_microsandbox", return_value=True),
            patch.object(registry, "MicrosandboxTerminal") as terminal_class,
        ):
            registry._get_terminal("user-uuid")
        terminal_class.assert_called_once_with("user-uuid")

    def test_delete_refuses_regular_workspaces(self):
        with self.assertRaises(registry.SandboxAdmissionError):
            registry.delete_integration_sandbox("user-uuid")

    def test_delete_removes_uncached_vm_and_revokes_reservation(self):
        self.reservations.keys.add(f"idea:isess:vm:{SANDBOX}")
        with (
            patch.object(registry, "_use_microsandbox", return_value=True),
            patch.object(registry, "remove_named_sandbox", return_value="deleted") as remove,
        ):
            self.assertEqual(registry.delete_integration_sandbox(SANDBOX), "deleted")
        remove.assert_called_once_with(SANDBOX)
        self.assertEqual(self.reservations.keys, set())

    def test_delete_destroys_cached_terminal_then_confirms_removal(self):
        terminal = Mock()
        registry._terminals[SANDBOX] = terminal
        with (
            patch.object(registry, "_use_microsandbox", return_value=True),
            patch.object(registry, "remove_named_sandbox", return_value="not_found"),
        ):
            self.assertEqual(registry.delete_integration_sandbox(SANDBOX), "deleted")
        terminal.destroy.assert_called_once()
        self.assertNotIn(SANDBOX, registry._terminals)

    def test_inventory_lists_only_integration_vms(self):
        with (
            patch.object(registry, "_use_microsandbox", return_value=True),
            patch.object(registry, "list_sandbox_names", return_value=[SANDBOX]) as names,
        ):
            self.assertEqual(registry.list_integration_sandboxes(), [SANDBOX])
        names.assert_called_once_with(registry.INTEGRATION_SANDBOX_PREFIX)


if __name__ == "__main__":
    unittest.main()
