# Founder / brand LinkedIn profile (document)

Rewrite a LinkedIn profile as a document a human pastes into LinkedIn. Adapted from Charlie Hills' profile-optimizer. This stack never logs into LinkedIn.

Deferred as a standing agent loop. Use when a human asks, or when tactic.studio needs a Pete-facing profile pack.

## Inputs

- brand-context for the company
- whose profile (name, role) — do not invent a person
- primary goal: booked calls | inbound | newsletter | hiring
- current headline / about / experience (paste or "start fresh")
- proof only from brand-context. Otherwise `[NEED:]`
- optional brand colours for image prompts

## Deliverables (code-block ready to paste)

1. **Headline** — 3 options, ≤220 characters (LinkedIn's current limit; verify if unsure). Lead with value + audience. Job titles optional for ranch industrial brands (tactic.studio may need the role for VP-of-marketing buyers).
2. **About** — Hook → struggle/empathy → method → authority → CTA. Full sentences. No fake metrics.
3. **Experience** — top 1–2 roles as story, not bullet salad. Context → challenge → action → result. Results need proof or `[NEED:]`.
4. **Featured (max 2)** — external links only (booking, site, newsletter, case study). Benefit-focused titles, 3–5 words.
5. **Image prompts** — banner 1584×396, avatar 400×400, two featured tiles 552×368. Generator-agnostic. Human attaches the headshot. ComfyUI preferred.

## Rules

- tactic.studio conversion CTAs stay human-gated (no "book now" to an unowned calendar unless the URL is real)
- Never fabricate client names or revenue
- Do not offer to post a launch update from this module
- Image prompts must be self-contained; no local paths
