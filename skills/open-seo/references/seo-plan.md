# SEO implementation plan

Turn an accepted (or fresh) review into a document a developer or marketer can apply on the **owned** target site. This is not a deploy script.

Adapted from OpenSEO's issue `howToFix` payloads and marketing-agi "ship artifacts, not advice."

## Hard rule

Every task is something a human does in the site CMS, repo, or hosting panel. The CRM agent does not SSH, commit to the product repo, edit DNS, or call a CMS API.

## Output JSON for the CRM writer

```json
{
  "title": "SEO Plan — example.com",
  "one_thing": "the week's implementation goal",
  "body": "markdown document",
  "tasks": [
    {
      "priority": 1,
      "effort": "S",
      "page": "https://example.com/",
      "task": "Add title tag",
      "implement": "exact markup or copy to paste",
      "verify": "view-source or rich-results test"
    }
  ]
}
```

`body` markdown skeleton:

```
# SEO Plan — {domain}
Derived from review: {review title} · Score {n}/100

## Do not implement from this agent
This document is for humans. The CRM will not change the live site.

## The one thing this week
## Implementation tasks
## Keyword pages to create (if any)
## Structured data to add
## GEO / AI extractability (llms.txt, factual chunks) — only if evidence supports it
## Out of scope / [NEED]
```

## Task quality bar

- Name the URL.
- Write the replacement title, meta, H1, alt text, or JSON-LD. "Improve the title" is not a task.
- Effort S (under an hour), M (half day), L (multi-day content).
- Never invent testimonials, ratings, or `AggregateRating` markup.
- tactic.studio outbound remains gated; plans must not include send/outreach tasks.

## Competitor reviews

Do not produce a plan. There is nothing to implement on a competitor's site.
