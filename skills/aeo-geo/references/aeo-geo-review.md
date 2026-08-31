# AEO / GEO review

Produce a one-page review anyone can read once and act on. The whole document supports **one action this week** across AEO (extractability) and GEO (citability).

## Inputs you will have

- Brand context excerpt
- Firecrawl markdown + metadata for 1–4 pages
- Heuristic extractability signals and issue rows (severity, evidence, how_to_fix)
- Optional seed questions for the brand (not live engine citation results)

You will **not** have live ChatGPT/Gemini citation panels, mention counts, or Search Console generative-AI exports unless a human pasted them.

## Register

- **Owned site:** internal fix-list for AEO + GEO. Concrete copy, structure, and entity markup a human can add.
- **Competitor:** strategy artefact. What they do for extractability and citability. No implementation tasks for their CMS.

## Output JSON for the CRM writer

```json
{
  "title": "AEO/GEO Review — example.com",
  "score": 58,
  "one_thing": "plain sentence of the week's action",
  "body": "markdown document",
  "prompt_panel": ["3 to 8 questions to track in the measurement panel, or empty"]
}
```

`body` markdown skeleton:

```
# AEO/GEO Review — {domain}
{date} · Score: XX/100 · Basis: Firecrawl extractability signals (no live citation vendor)

## The one thing
## AEO scorecard (extractability)
## GEO scorecard (citability readiness)
## Access & crawlers
## Entity kit
## Quotable pages & fan-out gaps
## Off-site corroboration [NEED]
## Measurement panel (prompts × engines)
## What I couldn't determine
## Method
```

## Section guidance

### AEO scorecard

- Answer-first structure under question-shaped headings
- Self-contained sections (pronouns resolved)
- Visible FAQ, tables, lists with extractable facts
- Stats/quotes with stated sources (Aggarwal et al. KDD 2024: citations + quotations + statistics help)

### GEO scorecard

- Entity clarity (Organization/Person JSON-LD, bios, sameAs)
- Quotable evidence density (not generic marketing prose)
- Fan-out pages for sub-questions
- Corroboration gaps — use `[NEED: prompt panel / press / reviews]`

### Access & crawlers

Document what the scrape suggests about HTML text vs JS-only content. Remind: allow Googlebot + OAI-SearchBot for search; training bots are a separate business decision.

## Guardrails

- Tone: calm and plain. Mentions ≠ citations.
- Gloss AEO, GEO, JSON-LD, OAI-SearchBot on first use.
- Never invent citation counts or engine rankings.
- tactic.studio outreach: document research gaps only — no outreach steps.
