---
name: review-code
description: Review and explore GitHub code repositories using Codex.
---

This skill specifies how to use Codex to explore code repositories.

## Overview
You have access to the command-line tool `codex`.  
`codex` is an AI coding agent that can read, modify, and execute code locally within a specified directory.

## Core Rules
- `codex` is **already installed** and ready to use.
- You must run `codex` in **exec mode** for all operations.
  - Example:  
    ```bash
    codex exec "Print to terminal a high-level overview of this repo"
    ```
- All work must occur inside **`${CODEX_SANDBOX}`**, using these paths:
  - Repositories: `${CODEX_SANDBOX}/repos`
  - Temporary files: `${CODEX_SANDBOX}/tmp`
- Never execute outside of `${CODEX_SANDBOX}` or modify system-level files.
- IMPORTANT: Confirm that `${CODEX_SANDBOX}` exists prior to running Codex.

## Example Workflow (Command Line)
cd ${CODEX_SANDBOX} # Navigate to Codex workspace
codex exec whoami # Confirm identity
cd ${CODEX_SANDBOX}/repos # Clone a repository to explore
git clone https://github.com/uhsealevelcenter/IDEA IDEA
cd ${CODEX_SANDBOX}/repos/IDEA # Enter repository and analyze
codex exec "Print to terminal a high-level overview of this repo"

## Example Codex Commands
codex exec "Print to terminal the full code for the data processing module"
codex exec "Summarize this repository structure and highlight the main entry points"
codex exec "Generate docstrings for all public functions in this package"
codex exec "Identify where to insert Codex integration hooks in this backend"

## Example Code Repositories
https://github.com/uhsealevelcenter/IDEA # Intelligent Data Exploring Assistant
https://github.com/uhsealevelcenter/Wyrtki-CSLIM # ENSO forecast model
https://github.com/uhsealevelcenter/QCSoft # UHSLC GUI tool for Quality Control

