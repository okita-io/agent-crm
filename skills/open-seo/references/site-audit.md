# Site audit / SEO review

Produce a one-page review anyone can read once and act on. The whole document supports **one action this week**. Everything else is supporting detail.

Adapted from OpenSEO `seo-audit` (plain-language report, one thing, small fixes, already working, method footer) and marketing-agi search scoring (title/meta, headings, indexability, AI extractability).

## Inputs you will have

- Brand context excerpt
- Firecrawl markdown + metadata for 1-4 pages
- Heuristic page signals and issue rows (severity, evidence, how_to_fix)
- Optional SearXNG hits for seed keywords (titles/snippets only, not volumes)

You will **not** have backlink counts, keyword difficulty, or Search Console.

## Register

- **Owned site:** internal fix-list. Concrete markup and copy.
- **Competitor:** sales/strategy artefact. What they are doing in titles, H1s, and topics. Do not write implementation tasks for their CMS.

## Output JSON for the CRM writer

```json
{
  "title": "SEO Review — example.com",
  "score": 62,
  "one_thing": "plain sentence of the week's action",
  "body": "markdown document",
  "keyword_focus": ["3 to 5 specific phrases, or empty"]
}
```

`body` markdown skeleton:

```
# SEO Review — {domain}
{date} · Score: XX/100 · Basis: Firecrawl page signals + SearXNG (no rank/backlink vendor)

## The one thing
## Scorecard
## Small fixes
## Already working
## Where to focus first
## What I couldn't determine
## Method
```

## Guardrails

- Tone: calm and plain. Severity words only where literally true (noindex on a homepage is critical; a long title is not).
- Gloss canonical, meta description, alt text, noindex, JSON-LD on first use.
- Skip nitpicks that do not matter for this site.
- Missing backlink or ranking data is "no recorded data", not a penalty.
- Favor specific intent phrases the site can win, not head terms a new site cannot rank for.
- Separate tool/heuristic findings from anything you inferred.
