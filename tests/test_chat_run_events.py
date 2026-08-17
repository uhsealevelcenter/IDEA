import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

import langgraph_service  # noqa: E402


class ChatRunEventTests(unittest.TestCase):
    def test_chat_requests_defer_to_the_configured_agent_model(self):
        request = langgraph_service.ChatRunRequest(
            session_id="chat-1",
            user_id="user-1",
            is_guest=False,
            messages=[{"role": "user", "content": "hello"}],
        )

        self.assertIsNone(request.model)
        self.assertEqual(langgraph_service.IDEA_AGENT_MODEL, "gpt-5.6-sol")

    def test_summarizes_only_current_run_model_usage(self):
        summary = langgraph_service._summarize_model_usage([
            {
                "run_id": "run-1", "input_tokens": 100,
                "cached_input_tokens": 40, "output_tokens": 20,
                "cache_write_input_tokens": 60,
                "total_tokens": 120, "model_image_count": 1,
            },
            {
                "run_id": "run-1", "input_tokens": 80,
                "output_tokens": 10, "total_tokens": 90,
                "model_image_count": 0,
            },
            {"run_id": "older", "total_tokens": 999},
        ], "run-1")

        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["input_tokens"], 180)
        self.assertEqual(summary["cached_input_tokens"], 40)
        self.assertEqual(summary["cache_write_input_tokens"], 60)
        self.assertEqual(summary["total_tokens"], 210)
        self.assertEqual(summary["model_image_count"], 1)

    def test_sequence_and_append_are_one_atomic_redis_operation(self):
        encoded = json.dumps({
            "seq": 1,
            "created_at": "2026-01-01T00:00:00",
            "chunk": "hello",
        })
        with patch.object(
            langgraph_service.redis_client, "eval", return_value=encoded
        ) as evaluate:
            event = langgraph_service._append_chat_run_event(
                "run-1", "hello"
            )

        self.assertEqual(event["seq"], 1)
        self.assertEqual(evaluate.call_args.args[1:4], (
            2,
            "langgraph_run_seq:run-1",
            "langgraph_run_events:run-1",
        ))

    def test_reads_only_events_after_the_consumed_sequence(self):
        raw_events = [
            json.dumps({"seq": 8, "chunk": "next"}),
            json.dumps({"seq": 9, "chunk": "later"}),
        ]
        with patch.object(
            langgraph_service.redis_client,
            "lrange",
            return_value=raw_events,
        ) as lrange:
            events = langgraph_service._list_chat_run_events(
                "run-1", after=7
            )

        lrange.assert_called_once_with(
            "langgraph_run_events:run-1", 7, -1
        )
        self.assertEqual([event["seq"] for event in events], [8, 9])

    def test_negative_sequence_starts_at_first_event(self):
        with patch.object(
            langgraph_service.redis_client, "lrange", return_value=[]
        ) as lrange:
            langgraph_service._list_chat_run_events("run-1", after=-5)

        lrange.assert_called_once_with(
            "langgraph_run_events:run-1", 0, -1
        )

    def test_stop_offloads_sandbox_interrupt_after_publishing_status(self):
        async def exercise():
            status_updates = []

            async def offload(function, user_id, run_id):
                self.assertIs(function, langgraph_service.interrupt_sandbox_run)
                self.assertTrue(status_updates)
                self.assertEqual(status_updates[-1][1]["status"], "stopping")
                return True

            with (
                patch.object(
                    langgraph_service,
                    "_get_chat_run_status",
                    return_value={
                        "run_id": "run-1",
                        "user_id": "user-1",
                        "status": "running",
                    },
                ),
                patch.object(langgraph_service.redis_client, "set"),
                patch.object(
                    langgraph_service,
                    "_update_chat_run_status",
                    side_effect=lambda run_id, **updates: status_updates.append(
                        (run_id, updates)
                    ),
                ),
                patch.object(langgraph_service, "_append_chat_run_event"),
                patch.object(
                    langgraph_service.asyncio,
                    "to_thread",
                    new_callable=AsyncMock,
                    side_effect=offload,
                ) as to_thread,
            ):
                result = await langgraph_service.stop_chat_run("run-1")

            self.assertTrue(result["sandbox_interrupted"])
            self.assertEqual(result["status"], "stopping")
            to_thread.assert_awaited_once_with(
                langgraph_service.interrupt_sandbox_run,
                "user-1",
                "run-1",
            )

        asyncio.run(exercise())


class MessageCheckpointTests(unittest.TestCase):
    def test_resolves_checkpoint_for_latest_visible_assistant_branch(self):
        with patch.object(
            langgraph_service,
            "_load_message_checkpoint",
            side_effect=lambda _thread, message_id: (
                ("branch-thread", "checkpoint-2")
                if message_id == "assistant-2"
                else None
            ),
        ):
            resolved = langgraph_service._resolve_graph_checkpoint(
                base_thread_id="base-thread",
                messages=[
                    {"id": "assistant-1", "role": "assistant"},
                    {"id": "assistant-2", "role": "assistant"},
                    {"id": "user-3", "role": "user"},
                ],
                response_message_id="assistant-3",
                run_id="run-3",
            )

        self.assertEqual(
            resolved,
            ("branch-thread", "checkpoint-2", "message_mapping"),
        )

    def test_root_regeneration_starts_an_isolated_branch(self):
        resolved = langgraph_service._resolve_graph_checkpoint(
            base_thread_id="base-thread",
            messages=[{"id": "user-1", "role": "user"}],
            response_message_id="new-assistant",
            run_id="run-2",
        )

        self.assertTrue(resolved[0].startswith("base-thread:branch:"))
        self.assertIsNone(resolved[1])
        self.assertEqual(resolved[2], "new_branch")

    def test_legacy_chat_uses_latest_thread_once_when_no_mapping_exists(self):
        with patch.object(
            langgraph_service,
            "_load_message_checkpoint",
            return_value=None,
        ):
            resolved = langgraph_service._resolve_graph_checkpoint(
                base_thread_id="base-thread",
                messages=[{"id": "old-assistant", "role": "assistant"}],
                response_message_id="new-assistant",
                run_id="run-2",
            )

        self.assertEqual(resolved, ("base-thread", None, "legacy_latest"))

    def test_idless_history_uses_latest_durable_checkpoint(self):
        with (
            patch.object(
                langgraph_service,
                "_load_message_checkpoint",
                return_value=None,
            ),
            patch.object(
                langgraph_service,
                "_load_latest_checkpoint",
                return_value=("branch-thread", "checkpoint-9"),
            ),
        ):
            resolved = langgraph_service._resolve_graph_checkpoint(
                base_thread_id="base-thread",
                messages=[{"id": "", "role": "assistant"}],
                response_message_id="new-assistant",
                run_id="run-10",
            )

        self.assertEqual(
            resolved,
            ("branch-thread", "checkpoint-9", "latest_idless_history"),
        )

    def test_stores_durable_message_checkpoint_mapping(self):
        with patch.object(langgraph_service.redis_client, "set") as set_value:
            langgraph_service._store_message_checkpoint(
                "base-thread",
                "assistant-1",
                "branch-thread",
                "checkpoint-1",
            )

        args = set_value.call_args.args
        stored = json.loads(args[1])
        self.assertEqual(stored["thread_id"], "branch-thread")
        self.assertEqual(stored["checkpoint_id"], "checkpoint-1")
        self.assertEqual(
            set_value.call_args.kwargs["ex"],
            langgraph_service.IDEA_CHECKPOINT_MAP_TTL_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
