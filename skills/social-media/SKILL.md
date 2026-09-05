---
name: social-media
description: Ranch content factory. Weekly post packages, newsletter drafts, content matrices, 7-day niche pulses, and visual briefs for MidnightSatin, Celestial-Nexus, HeyBuddy, and tactic.studio. Documents only from this skill — live publish is the separate publisher worker after human schedule. Adapted from charlie947/social-media-skills.
license: MIT
---

# Social media content for The Agency

Document-first content production. The CRM agent searches with SearXNG, scrapes with Firecrawl, and writes **content packages**. This skill does not post, schedule, comment, or log into LinkedIn / Instagram / TikTok / X / YouTube — the standing `publisher` worker sends only after a human schedules a `publish_jobs` row.

Craft modules (hooks, copy frameworks, slop removal, email sequences) stay in `skills/marketing-agi/`. Site audits stay in `skills/open-seo/`. This pack is the missing production calendar: what to ship this week, in whose voice, with which visual.

## Hard rules

1. **Documents, not deploys.** Output is markdown for humans. Never claim a post was published.
2. **Read brand-context first.** Use `brand-context.md` plus the per-brand file. Voice, proof, and off-limits live there.
3. **Never invent proof.** No fabricated metrics, testimonials, client logos, or "we did X." Write `[NEED: x]`.
4. **tactic.studio outbound is gated.** Pete (`pete@tactic.studio`) + naming-rights. Drafts only. Never send.
5. **No Apify, no social login, no Claude for Chrome.** Research uses SearXNG + Firecrawl. Graphics are ComfyUI prompts (or HTML files), not Gemini API calls.
6. **Do not duplicate marketing-agi.** For hooks use `references/hooks.md` there. For PAS/AIDA/BAB use `copy-frameworks.md`. Run every prose deliverable through `slop-patterns.md`.
7. **Honesty spine.** Scores are heuristics. State gaps. Flag claims that need a human before publish.

## Routing

| The agent / user wants... | Module | Also often needed |
|---|---|---|
| Deepen how a brand writes from real samples | `references/voice.md` | brand-context file |
| 32+ post ideas this month | `references/content-matrix.md` | `niche-pulse.md` |
| What's moving in the niche this week | `references/niche-pulse.md` | brand-context audience |
| A ready-to-review LinkedIn / X / IG post | `references/post-package.md` | marketing-agi `social.md`, `slop-patterns.md` |
| A newsletter issue draft (not a send) | `references/newsletter.md` | `voice.md`, marketing-agi `email.md` for sequences |
| Infographic, carousel, or HTML graphic brief | `references/visual-briefs.md` | the post package |
| Founder LinkedIn profile rewrite | `references/profile.md` | brand-context Voice |

Load only the module needed. Do not concatenate the pack into one prompt.

## What this skill will not do (say so)

- Post, schedule, or pin from inside this skill (publisher + human schedule only)
- Scrape LinkedIn or Instagram via Apify
- Drive a browser through Reddit / X feeds
- Call Gemini or other image APIs (prompts only; humans run ComfyUI)
- Score a draft against a live LinkedIn history unless a human uploaded an export
- Use Charlie Hills' pinned-comment persona (status-reversal memes about Claude)

Those absences belong in **What I couldn't determine**, not in faked engagement numbers.

## Content package skeleton

Every owned-social deliverable follows this shape (adapt per module):

```
# [Brand] — [platform] — [date]
Job: authority | engagement | distribution | conversion
Basis: [brand-context + samples + niche pulse / none]

## Hook (pre-fold)
[exact text to the truncation point]

## Body
[ready to paste, real line breaks]

## First comment
[link or resource — not in the post body]

## Visual brief
[or "none — text only"]

## Flagged
[claims that need a human before publish]

## What I couldn't determine
```

## Upstream (do not vendor as-is)

Collapsed into the modules above: `post-writer`, `content-matrix`, `newsletter-voice`, `voice-builder`, `niche-research`, `graphic-designer`, `gemini-infographic`, `gemini-carousel`.

Deferred: `profile-optimizer`, `reels-scripting`, `youtube-thumbnail`, `post-scorer`, `analytics-dashboard`.

Skipped: `post-formatter` (copy-frameworks), `hook-generator` (hooks.md), `quote-post` (voice clash), `pinned-comment` (persona). Keep only the **first-comment** pattern from that last skill.
