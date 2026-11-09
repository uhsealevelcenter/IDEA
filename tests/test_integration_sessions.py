import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

from idea_graph.identities import (  # noqa: E402
    INTEGRATION_SANDBOX_PREFIX,
    IntegrationContext,
    derive_execution_identities,
    validate_integration_session_id,
)
from integration_exports import ExportLimits, IntegrationExportStore  # noqa: E402
from integration_sessions import (  # noqa: E402
    STATE_CLOSING,
    STATE_DELETED,
    STATE_DELETING,
    STATE_OPEN,
    AdmissionError,
    IntegrationLimits,
    IntegrationSessionStore,
    IntegrationSessionSweeper,
    IntegrationSettings,
    SweepActions,
)

try:
    import fakeredis
except ImportError:  # pragma: no cover - optional test dependency
    fakeredis = None


def _ids(user="owner-1", integration="n8n-test", session="s-1", assistant=None, run="r"):
    return derive_execution_identities(
        user_id=user,
        chat_id="ignored",
        assistant_id=assistant,
        run_id=run,
        integration=IntegrationContext(integration, session),
    )


class IntegrationIdentityTests(unittest.TestCase):
    def test_same_session_reuses_vm_across_turns_and_specialists(self):
        first = _ids(assistant="coordinator", run="r1")
        second = _ids(assistant="tide-specialist", run="r2")

        self.assertEqual(first.sandbox_id, second.sandbox_id)
        self.assertEqual(first.workspace_id, second.workspace_id)
        self.assertNotEqual(first.thread_id, second.thread_id)
        self.assertNotEqual(first.kernel_id, second.kernel_id)
        self.assertTrue(first.sandbox_id.startswith(INTEGRATION_SANDBOX_PREFIX))
        self.assertLessEqual(len(first.sandbox_id.encode()), 128)

    def test_session_owner_and_integration_each_select_a_different_vm(self):
        base = _ids().sandbox_id
        self.assertNotEqual(base, _ids(session="s-2").sandbox_id)
        self.assertNotEqual(base, _ids(user="owner-2").sandbox_id)
        self.assertNotEqual(base, _ids(integration="n8n-other").sandbox_id)

    def test_structured_encoding_prevents_delimiter_collisions(self):
        self.assertNotEqual(
            _ids(integration='a","b', session="c").sandbox_id,
            _ids(integration="a", session='b","c').sandbox_id,
        )
        self.assertNotEqual(
            _ids(user="u", integration="x,y", session="z").sandbox_id,
            _ids(user="u", integration="x", session="y,z").sandbox_id,
        )

    def test_browser_mapping_is_unchanged(self):
        browser = derive_execution_identities(
            user_id="owner-1", chat_id="chat-1", assistant_id=None, run_id="r",
        )
        self.assertEqual(browser.sandbox_id, "owner-1")
        self.assertEqual(browser.scope, "browser")
        self.assertTrue(browser.thread_id.startswith("th_"))
        self.assertNotEqual(browser.workspace_id, _ids().workspace_id)

    def test_no_kernel_scope_falls_back_to_the_owner_wide_kernel(self):
        user_kernel = derive_execution_identities(
            user_id="owner-1", chat_id="c", assistant_id=None, run_id="r",
            kernel_scope="user",
        ).kernel_id
        for scope in ("user", "chat", "chat_assistant"):
            integration_kernel = derive_execution_identities(
                user_id="owner-1", chat_id="c", assistant_id=None, run_id="r",
                kernel_scope=scope,
                integration=IntegrationContext("n8n-test", "s-1"),
            ).kernel_id
            self.assertNotEqual(integration_kernel, user_kernel)

    def test_session_validation_rejects_without_normalizing(self):
        for bad in ("", "a\nb", "a\x00b", "a\u200bb", "x" * 257, None, 5):
            with self.assertRaises(ValueError):
                validate_integration_session_id(bad)
        self.assertEqual(validate_integration_session_id("é" * 128), "é" * 128)
        self.assertNotEqual(_ids(session=" s-1").sandbox_id, _ids().sandbox_id)

    def test_identity_secret_changes_vm_names(self):
        import os
        from unittest.mock import patch

        before = _ids().sandbox_id
        with patch.dict(os.environ, {"IDEA_IDENTITY_SECRET": "rotated"}):
            self.assertNotEqual(_ids().sandbox_id, before)


class FakeClock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@unittest.skipIf(fakeredis is None, "fakeredis[lua] is required")
class IntegrationSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.clock = FakeClock()
        self.limits = IntegrationLimits(daily_token_budget=10_000_000)
        self.store = self._store(self.limits)
        self.run_counter = 0

    def _store(self, limits):
        return IntegrationSessionStore(
            self.redis,
            IntegrationSettings(
                enabled=True, integration_ids=frozenset({"n8n-test"}), limits=limits,
            ),
            clock=self.clock,
        )

    def _admit(self, session="s-1", store=None, finish=True):
        self.run_counter += 1
        run_id = f"run-{self.run_counter}"
        ids = _ids(session=session)
        admission = (store or self.store).admit_turn(
            owner_user_id="owner-1",
            integration_id="n8n-test",
            workspace_id=ids.workspace_id,
            sandbox_id=ids.sandbox_id,
            run_id=run_id,
            input_bytes=10,
        )
        if finish:
            (store or self.store).finish_turn(ids.workspace_id, run_id, 1000)
        return admission

    def test_first_turn_creates_session_and_reserves_vm(self):
        admission = self._admit(finish=False)
        record = self.store.get(admission.workspace_id)

        self.assertTrue(admission.created)
        self.assertEqual(record.state, STATE_OPEN)
        self.assertTrue(record.vm_resident and record.vm_disk_reserved)
        self.assertIsNotNone(self.redis.get(f"idea:isess:vm:{admission.sandbox_id}"))

    def test_one_active_turn_per_conversation(self):
        self._admit(finish=False)
        with self.assertRaises(AdmissionError) as raised:
            self._admit(finish=False)
        self.assertEqual(raised.exception.status_code, 409)

    def test_stale_lease_is_reclaimed_and_charged(self):
        first = self._admit(finish=False)
        self.clock.advance(self.limits.run_lease_seconds + 1)
        self._admit()
        record = self.store.get(first.workspace_id)
        self.assertGreaterEqual(record.tokens_used, self.limits.run_token_allowance)

    def test_open_session_and_resident_vm_limits(self):
        self._admit(session="a", finish=False)
        self._admit(session="b", finish=False)
        with self.assertRaises(AdmissionError) as raised:
            self._admit(session="c")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.code, "vm_capacity")
        self.assertIsNotNone(raised.exception.retry_after)

        limits = IntegrationLimits(
            daily_token_budget=10_000_000, max_open_sessions=2, max_resident_vms=10,
        )
        store = self._store(limits)
        with self.assertRaises(AdmissionError) as raised:
            self._admit(session="c", store=store)
        self.assertEqual(raised.exception.code, "too_many_open_sessions")

    def test_disk_reservation_limit(self):
        limits = IntegrationLimits(
            daily_token_budget=10_000_000, max_resident_vms=10,
            vm_disk_mib=10240, max_reserved_disk_mib=20480,
        )
        store = self._store(limits)
        self._admit(session="a", store=store)
        self._admit(session="b", store=store)
        with self.assertRaises(AdmissionError) as raised:
            self._admit(session="c", store=store)
        self.assertEqual(raised.exception.code, "disk_capacity")

    def test_creation_and_message_rate_limits(self):
        limits = IntegrationLimits(
            daily_token_budget=10_000_000, new_sessions_per_hour=1,
            session_messages_per_minute=2,
        )
        store = self._store(limits)
        self._admit(session="a", store=store)
        self._admit(session="a", store=store)
        with self.assertRaises(AdmissionError) as raised:
            self._admit(session="a", store=store)
        self.assertEqual(raised.exception.code, "session_rate_limited")
        with self.assertRaises(AdmissionError) as raised:
            self._admit(session="b", store=store)
        self.assertEqual(raised.exception.code, "session_creation_rate_limited")

    def test_daily_budget_must_be_positive_and_is_enforced(self):
        with self.assertRaises(AdmissionError) as raised:
            self._admit(store=self._store(IntegrationLimits()))
        self.assertEqual(raised.exception.code, "daily_budget_exhausted")

        limits = IntegrationLimits(daily_token_budget=500, run_token_allowance=400)
        store = self._store(limits)
        ids = _ids()
        store.admit_turn(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=ids.workspace_id, sandbox_id=ids.sandbox_id,
            run_id="r1", input_bytes=1,
        )
        store.finish_turn(ids.workspace_id, "r1", 50)
        store.admit_turn(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=ids.workspace_id, sandbox_id=ids.sandbox_id,
            run_id="r2", input_bytes=1,
        )
        store.finish_turn(ids.workspace_id, "r2", 400)
        with self.assertRaises(AdmissionError):
            store.admit_turn(
                owner_user_id="owner-1", integration_id="n8n-test",
                workspace_id=ids.workspace_id, sandbox_id=ids.sandbox_id,
                run_id="r3", input_bytes=1,
            )

    def test_input_size_and_unknown_integration_are_rejected(self):
        ids = _ids()
        with self.assertRaises(AdmissionError) as raised:
            self.store.admit_turn(
                owner_user_id="owner-1", integration_id="n8n-test",
                workspace_id=ids.workspace_id, sandbox_id=ids.sandbox_id,
                run_id="r", input_bytes=self.limits.max_input_bytes + 1,
            )
        self.assertEqual(raised.exception.status_code, 413)
        with self.assertRaises(AdmissionError) as raised:
            self.store.admit_turn(
                owner_user_id="owner-1", integration_id="other",
                workspace_id=ids.workspace_id, sandbox_id=ids.sandbox_id,
                run_id="r", input_bytes=1,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_idle_and_absolute_expiry_close_the_session(self):
        admission = self._admit()
        self.clock.advance(self.limits.idle_expiry_seconds)
        with self.assertRaises(AdmissionError) as raised:
            self._admit()
        self.assertEqual(raised.exception.status_code, 410)
        self.assertEqual(self.store.get(admission.workspace_id).state, STATE_CLOSING)

        admission = self._admit(session="other")
        for _ in range(7):
            self.clock.advance(self.limits.idle_expiry_seconds - 60)
            try:
                self._admit(session="other")
            except AdmissionError as exc:
                self.assertEqual(exc.status_code, 410)
                break
        record = self.store.get(admission.workspace_id)
        self.assertEqual(record.close_reason, "absolute_lifetime")

    def test_ended_session_is_never_recreated(self):
        admission = self._admit()
        self.store.request_close(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=admission.workspace_id, sandbox_id=admission.sandbox_id,
        )
        with self.assertRaises(AdmissionError) as raised:
            self._admit()
        self.assertEqual(raised.exception.status_code, 410)

        unused = _ids(session="never-used")
        record = self.store.request_close(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=unused.workspace_id, sandbox_id=unused.sandbox_id,
        )
        self.assertEqual(record.state, STATE_DELETED)
        with self.assertRaises(AdmissionError):
            self._admit(session="never-used")

    def test_close_cancels_lease_renewal(self):
        admission = self._admit(finish=False)
        self.assertIsNone(
            self.store.renew_lease(admission.workspace_id, admission.run_id, admission.epoch)
        )
        self.store.request_close(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=admission.workspace_id, sandbox_id=admission.sandbox_id,
        )
        self.assertEqual(
            self.store.renew_lease(admission.workspace_id, admission.run_id, admission.epoch),
            "explicit_end",
        )

    def test_renewal_stops_runs_past_their_time_limit(self):
        admission = self._admit(finish=False)
        self.clock.advance(self.limits.max_run_seconds)
        self.assertEqual(
            self.store.renew_lease(admission.workspace_id, admission.run_id, admission.epoch),
            "run_time_limit",
        )

    def test_concurrent_admissions_never_exceed_vm_reservations(self):
        limits = IntegrationLimits(
            daily_token_budget=10_000_000, max_resident_vms=2,
            max_open_sessions=50, new_sessions_per_hour=100, messages_per_minute=100,
        )
        store = self._store(limits)
        admitted, rejected = [], []
        barrier = threading.Barrier(8)

        def worker(index):
            ids = _ids(session=f"race-{index}")
            barrier.wait()
            try:
                store.admit_turn(
                    owner_user_id="owner-1", integration_id="n8n-test",
                    workspace_id=ids.workspace_id, sandbox_id=ids.sandbox_id,
                    run_id=f"r{index}", input_bytes=1,
                )
                admitted.append(index)
            except AdmissionError:
                rejected.append(index)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(admitted), 2)
        self.assertEqual(len(rejected), 6)


@unittest.skipIf(fakeredis is None, "fakeredis[lua] is required")
class IntegrationSweeperTests(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.clock = FakeClock()
        self.limits = IntegrationLimits(daily_token_budget=10_000_000)
        self.store = IntegrationSessionStore(
            self.redis,
            IntegrationSettings(
                enabled=True, integration_ids=frozenset({"n8n-test"}), limits=self.limits,
            ),
            clock=self.clock,
        )
        self.calls = []
        self.delete_ok = True
        self.inventory = []
        self.sweeper = IntegrationSessionSweeper(
            self.store,
            SweepActions(
                stop_vm=lambda sandbox: self.calls.append(("stop", sandbox)) or True,
                delete_vm=self._delete,
                interrupt_run=lambda sandbox, run: self.calls.append(("interrupt", sandbox, run)),
                cancel_run=lambda run, reason: self.calls.append(("cancel", run, reason)),
                export_session=lambda record, final: self.calls.append(("export", record.sandbox_id)) or "complete",
                list_vms=lambda: list(self.inventory),
            ),
        )

    def _delete(self, sandbox_id):
        self.calls.append(("delete", sandbox_id))
        return self.delete_ok

    def _admit(self, session="s-1", finish=True):
        ids = _ids(session=session)
        admission = self.store.admit_turn(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=ids.workspace_id, sandbox_id=ids.sandbox_id,
            run_id=f"run-{session}", input_bytes=1,
        )
        if finish:
            self.store.finish_turn(ids.workspace_id, admission.run_id, 10)
        return admission

    def test_idle_vm_is_stopped_and_its_slot_released_before_expiry(self):
        admission = self._admit()
        self.clock.advance(self.limits.vm_stop_after_idle_seconds)
        self.sweeper.run_once()

        record = self.store.get(admission.workspace_id)
        self.assertEqual(record.state, STATE_OPEN)
        self.assertFalse(record.vm_resident)
        self.assertTrue(record.vm_disk_reserved)
        self.assertIn(("stop", admission.sandbox_id), self.calls)
        self.assertIsNone(self.redis.get(f"idea:isess:vm:{admission.sandbox_id}"))

    def test_expired_session_is_exported_then_deleted_exactly_once(self):
        admission = self._admit()
        self.clock.advance(self.limits.idle_expiry_seconds)
        self.sweeper.run_once()
        self.sweeper.run_once()

        record = self.store.get(admission.workspace_id)
        self.assertEqual(record.state, STATE_DELETED)
        self.assertEqual(record.close_reason, "idle_expiry")
        self.assertEqual(self.calls.count(("delete", admission.sandbox_id)), 1)
        self.assertEqual(self.calls.count(("export", admission.sandbox_id)), 1)
        self.assertEqual(self.redis.scard(self.store._scope_key("disks", "owner-1", "n8n-test")), 0)
        self.assertNotIn(admission.workspace_id, self.store.indexed_workspace_ids())

    def test_closing_session_interrupts_active_run_then_proceeds_after_grace(self):
        admission = self._admit(finish=False)
        self.store.request_close(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=admission.workspace_id, sandbox_id=admission.sandbox_id,
        )
        self.sweeper.run_once()
        self.assertIn(("interrupt", admission.sandbox_id, admission.run_id), self.calls)
        self.assertEqual(self.store.get(admission.workspace_id).state, STATE_CLOSING)

        self.clock.advance(self.limits.close_grace_seconds)
        self.sweeper.run_once()
        self.assertEqual(self.store.get(admission.workspace_id).state, STATE_DELETED)

    def test_failed_deletion_keeps_the_disk_reservation_and_retries(self):
        admission = self._admit()
        self.store.request_close(
            owner_user_id="owner-1", integration_id="n8n-test",
            workspace_id=admission.workspace_id, sandbox_id=admission.sandbox_id,
        )
        self.delete_ok = False
        self.sweeper.run_once()
        record = self.store.get(admission.workspace_id)
        self.assertEqual(record.state, STATE_DELETING)
        self.assertTrue(record.vm_disk_reserved)
        self.assertEqual(record.delete_attempts, 1)

        self.delete_ok = True
        self.sweeper.run_once()
        self.assertEqual(self.store.get(admission.workspace_id).state, STATE_DELETED)

    def test_inventory_reconciliation_removes_only_orphans(self):
        live = self._admit(session="live")
        self.inventory = [live.sandbox_id, "sb_n8n_orphan"]
        counts = self.sweeper.run_once()
        self.assertEqual(counts["orphans"], 1)
        self.assertIn(("delete", "sb_n8n_orphan"), self.calls)
        self.assertNotIn(("delete", live.sandbox_id), self.calls)

    def test_only_one_sweeper_leads(self):
        other = IntegrationSessionSweeper(self.store, self.sweeper.actions)
        self.assertTrue(self.sweeper.acquire_leadership())
        self.assertFalse(other.acquire_leadership())
        self.assertTrue(self.sweeper.acquire_leadership())


@unittest.skipIf(fakeredis is None, "fakeredis[lua] is required")
class IntegrationExportTests(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.clock = FakeClock()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IntegrationExportStore(
            self.redis, self.directory.name,
            ExportLimits(max_session_bytes=10, max_session_files=5, retention_seconds=100),
            clock=self.clock,
        )
        self.files = {
            "/outputs/plot.png": b"12345",
            "/outputs/.hidden": b"x",
            "/outputs/big.bin": b"0123456789AB",
        }
        self.reads = []

    def _metadata(self, _directory):
        return {path: f"{len(data)}:1.0" for path, data in self.files.items()}

    def _read(self, path):
        self.reads.append(path)
        return self.files[path]

    def _export(self, workspace="ws_a"):
        return self.store.export_outputs(
            workspace_id=workspace, list_metadata=self._metadata, read_bytes=self._read,
        )

    def test_selection_rule_caps_and_skips_hidden_files(self):
        outcome = self._export()
        self.assertEqual(outcome.exported, ["/outputs/plot.png"])
        self.assertIn("/outputs/big.bin", outcome.skipped)
        self.assertEqual(outcome.status, "partial")
        [item] = self.store.manifest("ws_a")
        self.assertEqual(item.size, 5)
        self.assertEqual(len(item.sha256), 64)

    def test_unchanged_files_are_not_reexported(self):
        self._export()
        self.reads.clear()
        self._export()
        self.assertEqual(self.reads, [])

    def test_public_download_uses_only_the_object_id(self):
        self._export("ws_a")
        [item] = self.store.manifest("ws_a")
        found = self.store.resolve_public(item.object_id)
        self.assertEqual(found[1].read_bytes(), b"12345")
        self.assertIsNone(self.store.resolve_public("../" + item.object_id))
        self.assertIsNone(self.store.resolve_public(item.object_id + "A"))
        self.assertIsNone(self.store.get_object("ws_b", item.object_id))
        self.clock.advance(101)
        self.assertEqual(self.store.purge_expired(), 1)
        self.assertIsNone(self.store.resolve_public(item.object_id))

    def test_downloads_resolve_only_through_the_owning_manifest(self):
        self._export("ws_a")
        [item] = self.store.manifest("ws_a")
        found = self.store.get_object("ws_a", item.object_id)
        self.assertEqual(found[1].read_bytes(), b"12345")
        self.assertIsNone(self.store.get_object("ws_b", item.object_id))
        self.assertIsNone(self.store.get_object("ws_a", "../ws_a/" + item.object_id))

    def test_exports_survive_until_retention_then_purge(self):
        self._export()
        self.clock.advance(50)
        self.assertEqual(len(self.store.manifest("ws_a")), 1)
        self.clock.advance(51)
        self.assertEqual(self.store.purge_expired(), 1)
        self.assertEqual(self.store.manifest("ws_a"), [])
        self.assertFalse((Path(self.directory.name) / "ws_a").exists())


if __name__ == "__main__":
    unittest.main()
