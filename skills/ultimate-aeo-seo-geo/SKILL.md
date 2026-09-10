---
name: ultimate-aeo-seo-geo
description: Ultimate SEO + AEO + GEO doctrine for Agency document reviews and plans. Never patch live pages. Extractability, evidence, entity, corroboration, access, technical checklist, anti-patterns, measurement.
license: MIT
---

# Ultimate AEO / SEO / GEO (Agency)

Document-first skill for The Agency. Write **reviews** and **plans** only. Never implement live site changes, send outreach, or invent metrics.

Companion to `aeo-geo` (operating order + crawler notes) and `open-seo` (classic audit). Prefer this pack for the expanded checklist and five-lever scorecard.

## Vocabulary

- **SEO** = blue-link rank
- **AEO** = extractable answers (snippets / some AI Overviews)
- **GEO** = cited or mentioned inside generated chat answers
- **GEO ≠ geography**

Google does not require extra “AI markup.” `llms.txt` is optional docs index, not a cheat. Schema is table stakes, not a citation formula.

## Hard rules (Agency)

1. Documents, not deploys
2. One action this week
3. Never invent rankings, citations, AI mention counts, or GSC numbers — use `[NEED: …]`
4. Verify against scraped evidence
5. Owned: review + plan. Competitor: review only
6. No outreach; tactic.studio remains Pete + naming-rights gated
7. Do not invent GrokBot / unknown crawler tokens

## Operating order

1. Access (HTML text, crawlers, sitemap/canonical)
2. Training vs search split (business decision)
3. Entity kit
4. Quotable / extractable pages
5. Fan-out sub-question pages
6. Off-site corroboration (no spam)
7. Optional short `/llms.txt`
8. Measure (prompt panel × engine × mode)

## Five GEO levers (heuristic 0–100)

1. **Extractability (~25%)** — answer in first 2–3 sentences under the question heading; self-contained sections; question-shaped headings; facts in lists/tables
2. **Specificity & evidence (~25%)** — sourced numbers/dates; first-party data; named entities; methodology
3. **Entity clarity (~20%)** — one canonical description; Organization/Person/Product JSON-LD; About as reference entry
4. **Corroboration (~20%)** — independent mentions, reviews, listicles (query engines to see who is cited)
5. **Machine access (~10%)** — intentional robots; clean HTML; optional llms.txt

Directional study only: Aggarwal et al., KDD 2024, arXiv:2311.09735 (evidence density beats keyword stuffing).

## Foundational technical checklist

- [ ] robots.txt intentional (search vs training bots)
- [ ] XML sitemap linked/fresh
- [ ] Canonicals correct
- [ ] Unique title + meta description
- [ ] Primary content in HTML (not JS-only)
- [ ] Clear H1 + heading hierarchy
- [ ] Internal links to answer/money pages
- [ ] Organization (+ Product/Article) JSON-LD valid
- [ ] FAQ visible if FAQ schema present
- [ ] Freshness/dates on time-sensitive claims
- [ ] Charts/images have text/table/alt fallback
- [ ] No cloaking / AI-only answer gates
- [ ] No accidental noindex on core pages
- [ ] HTTPS + clean redirects + mobile usable
- [ ] OG/Twitter cards
- [ ] About / author / contact for entity trust

## Traditional SEO (still required)

Intent match · URL/intent map · title/H1 alignment · thin/duplicate cleanup · internal links · technical hygiene · refresh vs net-new · backlink/mention **plans** only unless gated outreach asked.

**Content gate:** SHIP / FIX / BLOCK — clear claim, evidence, identifiable entity, no slop, sources linked, schema matches visible content.

## Anti-patterns

llms.txt stuffing · AI-only cloaking · FAQ schema without on-page answers · keyword stuffing for GEO · Reddit spam · invented citation % · inventing crawler tokens · GEO-as-geography

## Measurement

Prompt panel × engine × mode: mention | recommendation | citation | accuracy. GSC generative AI / Bing AI Performance / `utm_source=chatgpt.com` when available. Gaps go in **What I couldn't determine**.

## Attribution

Synthesized MIT/Apache doctrine for Agency local use: Agency aeo-geo + open-seo + marketing-agi geo levers; onvoyage-ai/gtm-engineer-skills; AgriciDaniel/claude-seo GEO guidance; aaron-he-zhu SEO/GEO ideas (Apache-2.0 — attribution kept). Skip NO-LICENSE packs for verbatim text.
