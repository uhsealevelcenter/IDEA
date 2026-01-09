## Working agreements

- You must run `codex` in **exec mode** for all operations.
  - Example:  
    ```bash
    codex exec "Print to terminal a high-level overview of this repo"
    ```
- All work must occur inside **`${CODEX_HOME}`**, using these paths:
  - Repositories: `${CODEX_HOME}/repos`
  - Temporary files: `${CODEX_HOME}/tmp`
- Never execute outside of `${CODEX_HOME}` or modify system-level files.
- Use clear, concise natural-language commands.
- Treat Codex as an agent you control via the command line — not a background service.