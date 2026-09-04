---
name: review-code
description: Review and explore code repositories using IDEA's Codex delegation tool.
---

# Review code with Codex

Use `delegate_to_codex` for substantial repository exploration and review. It
runs the Codex SDK inside the current user's isolated microVM and shares the
same private `/workspace` filesystem as IDEA's other execution tools.

- For review and investigation, use `access="read-only"`.
- Set `cwd` to the repository directory under `/workspace`.
- Give Codex a bounded question, the expected output, and concrete validation
  criteria. Ask it to cite paths and relevant symbols or line numbers.
- Use `access="workspace-write"` only when the user explicitly requested code
  changes. Ask Codex to run focused tests after editing.
- Do not invoke `codex exec` through the terminal. IDEA owns authentication,
  cancellation, thread resumption, and sandbox policy through the delegation
  tool.
- If `delegate_to_codex` is unavailable, explain that the feature is disabled
  and use IDEA's ordinary read/search/terminal tools.
