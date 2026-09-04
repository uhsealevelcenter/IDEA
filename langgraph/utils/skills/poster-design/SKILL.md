---
name: poster-design
description: Design polished, print-ready scientific posters using Python and/or frontend web tools. Use this skill when the user asks for a conference poster, research poster, academic poster, or large-format print layout.
---

# Poster Design

Use this skill for scientific, academic, conference, or large-format research posters.

## Default Deliverables

Unless the user asks otherwise, produce a short design brief, editable source files, supporting figure assets, and a print-ready PDF under IDEA's standard output directory: `/app/static/{user_id}/{session_id}`.

## Workflow

- Establish or infer the poster size, orientation, audience, constraints, and desired density. Design at final print size from the start.
- Create a concise design brief covering the poster goal, core takeaway, visual strategy, layout strategy, and production method.
- Triage content aggressively: prioritize the main finding, one or two dominant figures, short methods context, implications, and minimal references.
- Prefer Python for data graphics, maps, QR codes, and analysis-driven assets. Export figures as SVG/PDF when possible, or high-resolution PNG when raster is required.
- Prefer HTML/CSS for final poster composition unless the user requests Python-only. Use fixed physical dimensions, CSS Grid/Flexbox, print rules, zero browser margins, 100% scale, and background graphics enabled.
- Export a single-page PDF and verify the page size matches the target dimensions.

## Design Defaults

Use a clear grid, disciplined alignment, readable poster-scale typography, restrained color, and one or two strong focal areas. Avoid manuscript-length text, low-resolution screenshots, tiny figures, too many colors or fonts, and heavy boxed layouts unless the user asks for that style.

## Preflight

Before delivery, inspect the PDF for exact page size, page count, bounds, overlap, font rendering, image sharpness, vector crispness, contrast, scannable QR codes, and a clear 10-second takeaway.
