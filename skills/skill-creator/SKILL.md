---
name: skill-creator
description: Draft IDEA skill files for user review or download. Use when the user asks to create, revise, or propose a specialized IDEA skill; do not modify IDEA runtime files.
---

# Skill Creator

Use this skill when drafting proposed IDEA skills. Skills are concise, self-contained instruction files that help IDEA handle specialized workflows, datasets, tools, file formats, or domain procedures.

IDEA users cannot directly alter IDEA's runtime `utils`, `/app/skills`, or available-skills registry from within a normal IDEA session. Draft the skill content and present it to the user for review or download instead of attempting to install or register it.

## Required Structure

Draft one skill file:

- `SKILL.md`

If saving a file for the user, save it under IDEA's standard output directory with a clear path such as `<skill-name>/SKILL.md` or `<skill-name>_SKILL.md`, then provide a clickable download link.

The skill name should be lowercase kebab-case, specific, and stable. Avoid spaces, underscores, broad names, or names that duplicate an existing skill unless the user is revising that skill.

Every `SKILL.md` must begin with YAML frontmatter:

- `name`: exact skill folder/name.
- `description`: when to use the skill, including important trigger phrases and exclusions.

The body should contain only instructions IDEA would need at runtime. Do not add README, changelog, installation notes, or process documentation unless the user explicitly asks for them.

## Writing Guidelines

- Keep the skill concise and practical.
- Put the most important trigger and routing guidance near the top.
- Prefer checklists, workflows, examples, and validation rules over long background text.
- Include data sources, APIs, package names, paths, expected inputs, expected outputs, and caveats when they affect correct behavior.
- State when another IDEA skill should be used instead, or when skills are cross-listed.
- Use ASCII unless the existing file or scientific notation requires otherwise.

## Drafting Workflow

- Ask for missing purpose, trigger conditions, data sources, tools, expected inputs, expected outputs, and caveats only when they cannot be reasonably inferred.
- Draft the complete `SKILL.md` content.
- Show the draft to the user in the response, unless it is too long; for long drafts, summarize and provide a download link.
- When file output is useful, save the draft under IDEA's standard output directory and provide a clickable link.
- Do not modify `/app/skills`, `utils/system_prompt.py`, or other IDEA source files from an IDEA user session.

## Recommending Inclusion

Tell the user they may recommend that a new skill be included in IDEA by emailing `idea-dev-grp@hawaii.edu` and including the proposed `SKILL.md` content or downloaded skill file.

## Validation

Before finishing:

- Confirm the proposed skill name and frontmatter `name` match.
- Confirm `description` clearly says when to use the skill.
- Confirm the draft does not claim to be installed or registered.
- Check that the skill does not require unavailable files or tools.
- Confirm the user can view or download the proposed skill.
