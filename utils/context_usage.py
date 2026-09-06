from typing import Any


CONTEXT_LEVEL_NORMAL = "normal"
CONTEXT_LEVEL_WARNING = "warning"
CONTEXT_LEVEL_STOP = "stop"


def context_threshold_tokens(long_context_tokens: int, percent: float) -> int:
    return int(long_context_tokens * percent / 100)


def classify_context_usage(
    input_tokens: int,
    long_context_tokens: int,
    warning_percent: float,
    stop_percent: float,
) -> str:
    if input_tokens >= context_threshold_tokens(long_context_tokens, stop_percent):
        return CONTEXT_LEVEL_STOP
    if input_tokens >= context_threshold_tokens(long_context_tokens, warning_percent):
        return CONTEXT_LEVEL_WARNING
    return CONTEXT_LEVEL_NORMAL


def build_context_usage_state(
    usage: dict[str, Any],
    long_context_tokens: int,
    warning_percent: float,
    stop_percent: float,
) -> dict[str, Any]:
    input_tokens = int(usage.get("input_tokens") or 0)
    state = dict(usage)
    state.update(
        {
            "input_tokens": input_tokens,
            "long_context_input_threshold": long_context_tokens,
            "warning_percent": warning_percent,
            "warning_tokens": context_threshold_tokens(
                long_context_tokens,
                warning_percent,
            ),
            "stop_percent": stop_percent,
            "stop_tokens": context_threshold_tokens(
                long_context_tokens,
                stop_percent,
            ),
            "level": classify_context_usage(
                input_tokens,
                long_context_tokens,
                warning_percent,
                stop_percent,
            ),
        }
    )
    return state


def context_usage_message(state: dict[str, Any]) -> str | None:
    input_tokens = int(state.get("input_tokens") or 0)
    threshold = int(state.get("long_context_input_threshold") or 0)
    percent = (input_tokens / threshold * 100) if threshold else 0

    if state.get("level") == CONTEXT_LEVEL_STOP:
        return (
            f"This conversation has reached {percent:.1f}% of IDEA's long-context "
            f"input threshold ({input_tokens:,} of {threshold:,} tokens). This "
            "conversation needs to end. Start a new conversation to continue; "
            "further input in this conversation will not be sent to the LLM inference provider."
        )
    if state.get("level") == CONTEXT_LEVEL_WARNING:
        return (
            f"This conversation has reached {percent:.1f}% of IDEA's long-context "
            f"input threshold ({input_tokens:,} of {threshold:,} tokens). Please "
            "get to a good stopping point and start a new conversation soon."
        )
    return None
