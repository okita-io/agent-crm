---
name: open-seo
description: Write SEO review and implementation-plan documents for ranch brand sites. Never patch live pages. Site audits, competitor reviews, keyword focus, and GEO notes as markdown artifacts for humans.
license: MIT
---

# OpenSEO concepts for The Agency

Document-first SEO. The CRM agent scrapes with Firecrawl, searches with SearXNG, and writes **reviews** and **plans**. It does not implement changes on target sites, buy DataForSEO credits, connect Search Console, or post anything.

Use `references/site-audit.md` when writing a review. Use `references/seo-plan.md` when writing a plan. Load only the file you need.

## Hard rules

1. **Documents, not deploys.** Output is markdown stored in `seo_reviews` / `seo_plans`. A human applies it on the site.
2. **One action this week.** Reviews exist to support a single doable next step, plus a short fix list. Twenty undifferentiated findings have failed.
3. **Never invent rankings, traffic, backlinks, or GSC numbers.** Those need vendor data this stack does not have. Write `[NEED: Search Console / backlink crawl]` instead.
4. **Verify against scraped evidence.** Do not report a missing title if the scrape includes one.
5. **Owned vs competitor.** Owned sites get a review plus an implementation plan. Competitor sites get a review only. Never write a plan that would change someone else's site.
6. **Honesty spine.** Scores are heuristics. State gaps. No fabricated proof.

## Routing

| Job | Document | Module |
|-----|----------|--------|
| Audit our site | `SeoReview` kind `site_audit` | `references/site-audit.md` |
| Then tell humans how to implement | `SeoPlan` kind `mixed` | `references/seo-plan.md` |
| Study a competitor's organic surface | `SeoReview` kind `competitor` | `references/site-audit.md` (competitor register) |

## What this stack cannot do (and should say so)

- Rank tracking and estimated organic traffic (no DataForSEO / Ahrefs)
- Backlink graphs (no vendor crawl)
- Google Search Console imports (no OAuth)
- Lighthouse / Core Web Vitals (optional later; not required for the one-page review)

Those absences belong in **What I couldn't determine**, not in invented charts.
