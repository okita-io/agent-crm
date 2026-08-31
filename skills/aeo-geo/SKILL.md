---
name: aeo-geo
description: Write AEO and GEO review and implementation-plan documents for ranch brand sites. Never patch live pages. Answer extractability, entity clarity, quotable pages, fan-out, corroboration, and measurement as markdown artifacts for humans.
license: MIT
---

# AEO + GEO for The Agency

Document-first **answer-engine optimization (AEO)** and **generative-engine optimization (GEO)**. The Agency scrapes with Firecrawl and writes **reviews** and **plans**. It does not implement changes on target sites, send outreach email, or post on behalf of brands.

**Vocabulary (use consistently):**

- **SEO** = blue-link rank in traditional search results.
- **AEO** = extractable answers (featured snippets, some AI Overviews) — can a model lift a correct, self-contained answer from the page?
- **GEO** = being cited or mentioned inside generated chat answers (ChatGPT, Gemini, Perplexity, Copilot).

Google Search Central does **not** require extra “AI markup.” Ship crawlable HTML and people-first pages. Ignore llms.txt / GEO hacks as ranking signals. `llms.txt` is an optional docs index, not a cheat code. Schema is table stakes, not a citation formula.

Use `references/aeo-geo-review.md` when writing a review. Use `references/aeo-geo-plan.md` when writing a plan.

## Hard rules

1. **Documents, not deploys.** Output is markdown in `seo_reviews` / `seo_plans` with AEO/GEO kinds. A human applies it on the site.
2. **One action this week.** Reviews support a single doable next step.
3. **Never invent rankings, citations, AI mention counts, or Search Console numbers.** Use `[NEED: x]` for missing data.
4. **Verify against scraped evidence.** Do not claim missing FAQ if the scrape shows one.
5. **Owned vs competitor.** Owned sites get a review plus an implementation plan. Competitors get a review only.
6. **No live site changes.** This stack never patches robots.txt, JSON-LD, or copy on customer domains.
7. **tactic.studio outreach** — research and document only. No outreach; Pete + naming-rights gate remains.

## Operating order (teach this sequence)

1. **Access** — Allow Googlebot, OAI-SearchBot, PerplexityBot, Claude-SearchBot; WAF not blocking crawler IPs; important copy in HTML text (not JS-only).
2. **Split training vs search** — GPTBot / ClaudeBot / Google-Extended / Applebot-Extended are independent of search appearance. Business decision, not a default block.
3. **Entity kit** — Organization/Person JSON-LD, bios, `sameAs`; Wikipedia only if legitimate.
4. **Quotable pages** — Answer up top; stats, quotes, sources, tables, visible FAQ. GEO paper (Aggarwal et al., KDD 2024) found cite-sources / quotations / statistics help; keyword stuffing failed.
5. **Fan-out pages** — Sub-questions (pricing, vs, how-to, industrial AR training, WebAR, 8th Wall, etc.).
6. **Off-site corroboration** — Reviews, journalism. Do not spam Reddit. tactic.studio outreach: docs only.
7. **Optional short `/llms.txt`** — Docs index, not a ranking signal.
8. **Measure** — Prompt panel × engine × mode (mention / recommendation / citation / accuracy). GSC generative-AI reports; Bing Webmaster Tools AI Performance (they call it GEO). Referrals: `utm_source=chatgpt.com`.

## Crawler notes (encode; do not invent others)

| Token / topic | Note |
|---------------|------|
| ChatGPT-User / Perplexity-User | Often ignore robots |
| Claude-User | Honors robots (Anthropic docs) |
| Google-Extended | Does **not** control AI Overviews; GSC “Search generative AI” control does (worldwide as of 2026-08-31) |
| Bing | NOARCHIVE / NOCACHE / `data-nosnippet` for Copilot |
| Applebot vs Applebot-Extended | `nosnippet` removes Apple Intelligence context |
| xAI / Grok | No official webmaster robots contract — do not invent a GrokBot token |
| Citation formula | Nobody publishes one; schema is interpretation, not a cheat code |
| OAI-SearchBot | Required for ChatGPT search visibility |

## Routing

| Job | Document | Reference |
|-----|----------|-----------|
| AEO/GEO audit (owned) | `SeoReview` kind `geo` | `references/aeo-geo-review.md` |
| Human implementation brief | `SeoPlan` kind `geo` | `references/aeo-geo-plan.md` |
| Competitor citability study | `SeoReview` kind `geo` (competitor register) | `references/aeo-geo-review.md` |

## What this stack cannot do

- Live citation rank or mention share across engines (no vendor panel)
- Automated prompt-panel runs against ChatGPT/Gemini/Perplexity (human runs the panel)
- Wikipedia page creation
- Reddit or forum posting
- Changing robots.txt or markup on live sites

Those gaps belong in **What I couldn't determine**, not invented metrics.
