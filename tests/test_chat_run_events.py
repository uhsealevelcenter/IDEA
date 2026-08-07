import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

import langgraph_service  # noqa: E402


class ChatRunEventTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
