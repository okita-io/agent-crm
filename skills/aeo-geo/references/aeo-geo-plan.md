# AEO / GEO implementation plan

A human applies this on the owned site. The Agency never deploys it.

## Output JSON for the CRM writer

```json
{
  "title": "AEO/GEO Plan — example.com",
  "one_thing": "plain sentence",
  "body": "markdown document",
  "tasks": [{
    "priority": 1,
    "effort": "S",
    "page": "url",
    "task": "...",
    "implement": "exact copy or markup to add",
    "verify": "how a human confirms on the live site"
  }]
}
```

## Plan skeleton

```
# AEO/GEO Plan — {domain}
Derived from: {review title}

## Do not implement from this agent
## The one thing this week
## 1. Access & robots (human applies)
## 2. Entity kit (JSON-LD, bios, sameAs)
## 3. Quotable page rewrites
## 4. Fan-out pages to create
## 5. Optional /llms.txt
## 6. Measurement panel setup
## Out of scope / [NEED]
```

## Task rules

Each task must include:

- Page URL
- Exact copy or markup to add (not vague “improve SEO”)
- Effort S/M/L
- Verify step a human runs in a browser

Order tasks following the operating sequence: access → entity → quotable pages → fan-out → llms.txt → measurement.

## robots.txt guidance (human decision)

When suggesting crawler rules, separate:

- **Search appearance:** Googlebot, OAI-SearchBot, PerplexityBot, Claude-SearchBot
- **Training / extended:** GPTBot, ClaudeBot, Google-Extended, Applebot-Extended

Never claim the CRM updated robots.txt.

## Measurement section

List 5–10 prompt-panel questions × engines (ChatGPT, Gemini, Perplexity, Copilot) × modes (mention / recommendation / citation / accuracy). Mark live results `[NEED: human panel run]`.

## Out of scope

- Reddit spam, purchased citations, llms.txt hacks
- tactic.studio outreach (Pete + naming-rights)
- Invented GSC or Bing AI Performance numbers
