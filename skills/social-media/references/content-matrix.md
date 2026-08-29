# Content matrix

Pair 3–5 content pillars with 8 formats to produce a table of specific post ideas. Adapted from the Justin Welsh / Charlie Hills matrix. The deliverable is a markdown document, not a chat brainstorm.

## Pillars

Read `brand-context.{brand}.md`. Derive 3–5 pillars from positioning, audience, and "words we use." Confirm they are distinct. More than 5 dilutes the matrix.

If brand-context is too thin, stop and write `[NEED: 3 content pillars]` rather than inventing a category the brand does not own.

## Formats (columns, always this order)

1. **Actionable** — one ultra-specific how-to
2. **Motivational** — a real story with a cost; skip if brand-context forbids personal / invented heroics
3. **Analytical** — why something works
4. **Contrarian** — against common advice in the niche, actually held
5. **Observation** — an under-discussed trend the brand is allowed to notice
6. **X vs Y** — two named entities (tools, tropes, vendors, formats)
7. **Present vs Future** — current state vs a specific prediction, with the why
8. **Listicle** — resources, mistakes, or steps (exactly one job)

## Cell quality

Each cell is a **headline**, not a theme. Good: "The 8th Wall sunset is a training-module problem, not a lens problem." Bad: "WebAR tips."

Do not reuse the same idea across pillars. Tune language to Voice. Never invent proof — if a cell needs a number we do not have, write the headline with `[NEED: metric]`.

Skip Motivational cells that would require a fake founder story. Leave the cell as `[NEED: real story]` instead of padding.

## Output

Write to a content-package document (or `content-matrix-{brand}-{date}.md`):

```
# Content matrix — [brand] — [date]
Pillars from: brand-context.[brand].md
Pulse: [niche-pulse doc or "none"]

| Pillar | Actionable | Motivational | Analytical | Contrarian | Observation | X vs Y | Present vs Future | Listicle |
|--------|------------|--------------|------------|------------|-------------|--------|-------------------|----------|
| ...    | headline   | ...          | ...        | ...        | ...         | ...    | ...               | ...      |

## Strongest cell
[pillar × format]: [headline]. Why this one first.

## Flagged
[cells that need proof, legal, or Pete]
```

## Next move

Hand the strongest 3 cells to `post-package.md`. Do not write 32 posts in one pass.
