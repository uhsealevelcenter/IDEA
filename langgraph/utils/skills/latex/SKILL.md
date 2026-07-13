---
name: latex
description: Create, edit, compile, or troubleshoot LaTeX documents and generated PDFs in IDEA, including reports, manuscripts, Beamer slides, equations, tables, figures, citations, and .tex-to-PDF workflows.
---

# LaTeX

Use this skill when working with `.tex` source, LaTeX errors, or PDF outputs generated from LaTeX.

## Environment

IDEA includes `pdflatex`, `latexmk`, TeX Live LaTeX packages, bibliography tooling, `ghostscript`, `poppler-utils`, and `chktex`. Do not use `pip install pdflatex` as a substitute for the system LaTeX toolchain.

## Workflow

- Save generated `.tex`, assets, logs, and PDFs under IDEA's standard output directory: `/app/static/{user_id}/{session_id}`.
- Prefer `latexmk -pdf -interaction=nonstopmode -halt-on-error` for compilation.
- If using `pdflatex`, run it at least twice when the document has references, citations, a table of contents, or cross-references.
- Verify the final PDF exists and has nonzero size before returning a link.
- On failure, inspect the `.log` file and fix the first meaningful LaTeX error before retrying.

## Defaults

For reports, default to `article` with common packages such as `geometry`, `amsmath`, `amssymb`, `graphicx`, `booktabs`, `xcolor`, `hyperref`, and `enumitem`. Use conservative formatting unless the user asks for a specific style.
