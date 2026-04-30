# IDEA Interpreter

This package is IDEA's local interpreter runtime. It is closely based on
[Open Interpreter](https://github.com/openinterpreter/open-interpreter), version
0.4.3, with IDEA-specific changes for the web application runtime.

The original Open Interpreter project is licensed under the GNU Affero General
Public License v3.0. A copy of that license is included in this folder as
`LICENSE`.

## Why IDEA Vendors This Code

IDEA previously installed a patched Open Interpreter fork directly from GitHub.
That made deployment depend on an external source checkout and made it harder to
iterate on behavior that is specific to IDEA's chat UI, execution lifecycle, and
conversation persistence.

This vendored interpreter is intended to be reduced over time toward the runtime
path IDEA actually relies on: streaming model output, executing generated code,
collecting console/image outputs, and returning those outputs to the model. Some
upstream modules remain for import compatibility while this migration settles.
Keeping the interpreter in the IDEA repository lets us:

- control exactly which interpreter code is deployed with IDEA;
- patch Responses API behavior without maintaining a separate GitHub dependency;
- preserve correct turn-by-turn state when code execution, plots, and other tool
  outputs are returned to the assistant;
- gradually simplify the interpreter around IDEA's application needs.

## Current IDEA-Specific Behavior

The active model path uses LiteLLM's Responses API adapter. It records the prior
Responses `response.id` and sends it as `previous_response_id` on the next turn.
When the model calls IDEA's `execute` tool, the interpreter preserves the
Responses `call_id` and converts console, image, or other execution output into
`function_call_output` items before continuing the model turn.

For mixed outputs from one code execution, such as text, then a plot, then more
text, the converter aggregates all console STDOUT/STDERR chunks for the same
`call_id` into one `function_call_output`. Image output is still sent as visual
context, but it should not replace useful text output such as file paths,
summary statistics, warnings, or printed diagnostics. Stored histories are also
normalized so computer output chunks that lost their `call_id` can inherit it
from the surrounding execution group.

This is intentionally different from replaying tool calls as assistant messages.
The Responses API expects tool output to be tied to the original function call,
and IDEA's conversation history must not rewrite those tool-call messages into
ordinary assistant chat.

## Provider Compatibility

This code is expected to work best with models/providers that LiteLLM supports
through an OpenAI-compatible Responses API implementation, including function
calling/tool calling and streamed response events. IDEA currently sets its
production path to that behavior because it needs `previous_response_id` and
`function_call_output` handling.

Providers that only expose Chat Completions may need a separate fallback path.
Some legacy Chat Completions conversion code remains in this package, but the
tested IDEA path is the Responses path. Before switching IDEA to another
inference provider, verify that LiteLLM supports:

- streamed responses for the selected model;
- function/tool calls for `execute`;
- image input if plot/image feedback should be visible to the model;
- `previous_response_id` or an equivalent provider-side conversation mechanism.

## Conversation Loop Notes

Open Interpreter's original loop is designed for a terminal workflow where the
assistant is repeatedly prompted to decide whether to continue, ask for more
information, or stop. IDEA uses a chat UI, so prompts such as "what's next" or
"are we done" can leak into user-visible assistant responses and make the
conversation feel awkward.

As an initial polish step, fallback console-output context now uses a developer
message rather than adding "are we done" wording to user text:

> The previous User message contains console STDOUT/STDERR from code execution.
> Use it to decide whether to continue with execute(), provide an answer,
> explain its meaning, or otherwise communicate with the User.

The next refinement should separate internal continuation control from
user-facing prose. Options to evaluate:

- keep continuation prompts as developer/internal context only;
- suppress boilerplate completion phrases from the streamed UI when they are
  purely control signals;
- replace "are we done" prompts with a more neutral internal instruction that
  asks the model to either continue with another `execute` call or provide a
  final user-facing answer;
- add explicit state in the interpreter for "tool output received, assistant
  should summarize or continue" rather than relying on conversational nudges.
