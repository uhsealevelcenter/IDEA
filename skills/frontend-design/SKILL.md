---
name: frontend-design
description: Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, artifacts, posters, or applications (examples include websites, landing pages, dashboards, React components, HTML/CSS layouts, or when styling/beautifying any web UI). Generates creative, polished code and UI design that avoids generic AI aesthetics.
---

This skill specifies how to produce distinctive, production-grade frontend interfaces while avoiding generic, low-signal aesthetics. Deliver working code with rigorous attention to visual detail and deliberate creative decisions.

The user provides frontend requirements: a component, page, application, or interface to build. They may include context about the purpose, audience, or technical constraints.

## Design Thinking

Before coding, analyze the context and commit to a clear, high-contrast aesthetic direction:
- **Purpose**: What problem does this interface solve? Who uses it?
- **Tone**: Select a strong, falsifiable stance: brutally minimal, maximalist, retro-futuristic, organic, luxury, playful, editorial, brutalist, art deco, pastel, industrial, etc. Use these as priors, then refine to a single, consistent visual hypothesis.
- **Constraints**: Technical requirements (framework, performance, accessibility).
- **Differentiation**: Identify the primary memory anchor. What will a viewer recall after one glance?

**CRITICAL**: Choose a clear conceptual direction and execute it with precision. Maximalism and minimalism are both valid; the non-negotiable is intentionality.

Then implement working code (HTML/CSS/JS, React, Vue, etc.) that is:
- Production-grade and functional
- Visually striking and memorable
- Cohesive with a clear aesthetic point-of-view
- Meticulously refined in every detail

## Frontend Aesthetics Guidelines

Focus on:
- **Typography**: Choose fonts that are beautiful, unique, and interesting. Avoid generic fonts like Arial and Inter; opt instead for distinctive choices that elevate the frontend's aesthetics; unexpected, characterful font choices. Pair a distinctive display font with a refined body font.
- **Color & Theme**: Commit to a cohesive aesthetic. Use CSS variables for consistency. Dominant colors with sharp accents outperform timid, evenly-distributed palettes.
- **Motion**: Use animations for effects and micro-interactions. Prioritize CSS-only solutions for HTML. Use Motion library for React when available. Focus on high-impact moments: one well-orchestrated page load with staggered reveals (animation-delay) creates more delight than scattered micro-interactions. Use scroll-triggering and hover states that surprise.
- **Spatial Composition**: Unexpected layouts. Asymmetry. Overlap. Diagonal flow. Grid-breaking elements. Generous negative space OR controlled density.
- **Backgrounds & Visual Details**: Create atmosphere and depth rather than defaulting to solid colors. Add contextual effects and textures that match the overall aesthetic. Apply creative forms like gradient meshes, noise textures, geometric patterns, layered transparencies, dramatic shadows, decorative borders, custom cursors, and grain overlays.

NEVER use generic AI-generated aesthetics such as overused font families (Inter, Roboto, Arial, system fonts), clichéd color schemes (especially purple gradients on white), predictable layouts, or cookie-cutter patterns that lack context-specific character.

Interpret the brief creatively and make unexpected choices that are defensible within the context. No two outputs should converge. Vary light vs. dark, typographic pairings, and overall aesthetic regimes. Avoid recurrent defaults (e.g., Space Grotesk) across generations.

**IMPORTANT**: Match implementation complexity to the aesthetic vision. Maximalist designs warrant elaborate code and effects; minimalist or refined designs require restraint, precision, and careful handling of spacing, typography, and subtle detail. Elegance is a function of execution quality.

Remember: IDEA is capable of exceptional creative output. Commit fully to a distinctive vision and execute with discipline.

**CRITICAL**: Save the web page as a self-contained HTML file (embedded CSS + JS; external assets allowed/ CDNs permitted, prefer pinned versions.) and provide a clickable link.
