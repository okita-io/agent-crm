# Niche pulse

Surface the most relevant stories in a brand's niche from the **last 7 days**. Adapted from Charlie Hills' niche-research skill. This stack uses SearXNG + Firecrawl, not Claude for Chrome and not a logged-in X/Reddit session.

## Window

Today is the review date. Exclude anything older than 7 days without exception. If a publish date is missing, unclear, or only a scrape timestamp, exclude the item and note it under gaps.

Never invent links, metrics, or dates.

## Queries

Build 6–10 SearXNG queries from brand-context audience + pillars. Mix:

- `{niche} news`
- `{niche} launch`
- `{niche} controversy` (skip if brand-context off-limits)
- `{niche} reddit`
- named competitor + `announcement`
- community surfaces the hunter already catalogued (subreddit names, newsletters)

Prefer `site:` filters the ranch already uses. Do not log into Facebook, LinkedIn, or X.

## Verify

For each promising hit:

1. Scrape with Firecrawl when the SERP snippet is not enough
2. Locate a visible publish date
3. Keep only in-window items
4. Group into themes that have at least two of: attention, disagreement, novel information, real implication for the brand

Target up to **20 themes**. Fewer is acceptable. Do not pad.

## Output

```
# Niche pulse — [brand] — [date]
Window: last 7 days · Source: SearXNG + Firecrawl
As of [YYYY-MM-DD]

| Theme | Sources | Representative URLs | Attention signals (quoted, not invented) | What's debated | Why it matters for [brand] | Shareable angle |
|-------|---------|---------------------|------------------------------------------|----------------|----------------------------|-----------------|

## What I couldn't determine
[feeds we could not scroll, dates we could not verify, paywalled pages]

## Hand off
[2–5 rows worth turning into content-matrix cells or post packages]
```

Attention signals must be quoted from the page ("2.1k comments") or omitted. Do not estimate virality.

## Rules

- Table is the deliverable. Short gaps section after, no essay.
- If SearXNG returns stale results, say the pulse is thin. Do not backfill from training data.
- tactic.studio pulses stay on industrial AR / retail / F&B marketing — not agency gossip.
- MidnightSatin pulses stay on romance serials / BookTok craft — not Kindle Unlimited framing.
