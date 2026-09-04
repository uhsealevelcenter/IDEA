import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from msb_sandbox import MicrosandboxTerminal, _positive_int_env


class ResourceLimitConfigurationTests(unittest.TestCase):
    def test_positive_integer_environment_setting(self):
        with patch.dict(os.environ, {"SANDBOX_TEST_LIMIT": "2048"}):
            self.assertEqual(
                _positive_int_env("SANDBOX_TEST_LIMIT", 1),
                2048,
            )

    def test_positive_integer_environment_setting_uses_default(self):
        for environment in ({}, {"SANDBOX_TEST_LIMIT": ""}):
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(
                        _positive_int_env("SANDBOX_TEST_LIMIT", 4096),
                        4096,
                    )

    def test_invalid_resource_settings_fail_clearly(self):
        for raw_value in ("0", "-1", "not-a-number"):
            with self.subTest(raw_value=raw_value):
                with patch.dict(
                    os.environ,
                    {"SANDBOX_TEST_LIMIT": raw_value},
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "SANDBOX_TEST_LIMIT must be a positive integer",
                    ):
                        _positive_int_env("SANDBOX_TEST_LIMIT", 1)

    def test_new_sandbox_receives_cpu_memory_and_disk_limits(self):
        calls = []

        class SandboxNotFoundError(Exception):
            pass

        class FakeImage:
            @staticmethod
            def oci(reference, *, upper_size_mib):
                return {
                    "reference": reference,
                    "upper_size_mib": upper_size_mib,
                }

        class FakeSandbox:
            @staticmethod
            async def get(_name):
                raise SandboxNotFoundError

            @staticmethod
            async def create(name, **kwargs):
                calls.append((name, kwargs))
                return "sandbox-handle"

        microsandbox = types.ModuleType("microsandbox")
        microsandbox.Image = FakeImage
        microsandbox.Sandbox = FakeSandbox
        errors = types.ModuleType("microsandbox.errors")
        errors.SandboxNotFoundError = SandboxNotFoundError

        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal.session_id = "resource-test"
        terminal.image = "registry.example/idea:immutable"
        terminal.cpus = 2
        terminal.memory = 4096
        terminal.disk_mb = 10240
        terminal.idle_timeout = 1800
        terminal.max_duration = None
        terminal.shared_data_host_path = ""
        terminal._run = lambda factory: asyncio.run(factory())

        with patch.dict(
            sys.modules,
            {
                "microsandbox": microsandbox,
                "microsandbox.errors": errors,
            },
        ):
            terminal._connect_or_create()

        self.assertEqual(terminal._sandbox, "sandbox-handle")
        self.assertEqual(len(calls), 1)
        name, kwargs = calls[0]
        self.assertEqual(name, "resource-test")
        self.assertEqual(kwargs["cpus"], 2)
        self.assertEqual(kwargs["memory"], 4096)
        self.assertEqual(
            kwargs["image"],
            {
                "reference": "registry.example/idea:immutable",
                "upper_size_mib": 10240,
            },
        )

    def test_existing_sandbox_is_reconnected_without_reprovisioning(self):
        class SandboxNotFoundError(Exception):
            pass

        class ExistingSandbox:
            status = "running"

            async def refresh(self):
                return None

            async def connect(self):
                return "existing-sandbox-handle"

        class FakeImage:
            @staticmethod
            def oci(*_args, **_kwargs):
                raise AssertionError("existing sandbox must not rebuild its disk")

        class FakeSandbox:
            @staticmethod
            async def get(_name):
                return ExistingSandbox()

            @staticmethod
            async def create(*_args, **_kwargs):
                raise AssertionError("existing sandbox must not be recreated")

        microsandbox = types.ModuleType("microsandbox")
        microsandbox.Image = FakeImage
        microsandbox.Sandbox = FakeSandbox
        errors = types.ModuleType("microsandbox.errors")
        errors.SandboxNotFoundError = SandboxNotFoundError

        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal.session_id = "existing-resource-test"
        terminal._run = lambda factory: asyncio.run(factory())

        with patch.dict(
            sys.modules,
            {
                "microsandbox": microsandbox,
                "microsandbox.errors": errors,
            },
        ):
            terminal._connect_or_create()

        self.assertEqual(terminal._sandbox, "existing-sandbox-handle")

    def test_compose_profiles_define_expected_defaults(self):
        repository = Path(__file__).resolve().parents[1]
        base = (repository / "docker-compose.yml").read_text()
        production = (repository / "docker-compose.prod.yml").read_text()

        for setting in (
            "SANDBOX_CPUS=${SANDBOX_CPUS:-1}",
            "SANDBOX_MEMORY_MB=${SANDBOX_MEMORY_MB:-1024}",
            "SANDBOX_DISK_MB=${SANDBOX_DISK_MB:-4096}",
        ):
            self.assertIn(setting, base)
        for setting in (
            "SANDBOX_CPUS=${SANDBOX_CPUS:-2}",
            "SANDBOX_MEMORY_MB=${SANDBOX_MEMORY_MB:-4096}",
            "SANDBOX_DISK_MB=${SANDBOX_DISK_MB:-10240}",
        ):
            self.assertIn(setting, production)


if __name__ == "__main__":
    unittest.main()
