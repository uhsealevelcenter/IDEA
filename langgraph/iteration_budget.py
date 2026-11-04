"""Transient model guidance shared by the graph and manual agent loops."""


def iteration_budget_message(iteration: int, max_iterations: int) -> str | None:
    remaining = max_iterations - iteration + 1
    if remaining > 5:
        return None
    prefix = (
        f"IDEA execution budget: model call {iteration} of {max_iterations}; "
        f"{remaining} call(s) remain including this one. "
        "The last call is reserved for a user-facing check-in without tools. "
    )
    if remaining <= 1:
        return prefix + (
            "Use no tools. Summarize verified work completed and usable outputs, "
            "including file links where available. State what remains or is uncertain, "
            "and ask whether the user wants to continue. Do not claim unfinished "
            "work is complete or promise to continue automatically."
        )
    if remaining <= 3:
        return prefix + (
            "Wrap up now: finish only essential work feasible within the remaining "
            "tool-enabled calls, preserve usable outputs, and avoid starting new "
            "investigations or long delegations. Prepare a concise handoff for the user. "
            "If the objective is fully complete, provide the final answer now."
        )
    return prefix + (
        "Prioritize the essential remaining steps and saving useful outputs. "
        "Avoid expanding scope; finish the objective now if feasible, otherwise "
        "prepare to pause and check in with the user before the safety limit."
    )
