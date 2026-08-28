# Ranch brand context (overview)

Shared constraints for Research, Hunter, and dashboard-side agents using `skills/marketing-agi/`.

## Brands

| Brand | Slug | Context file |
|-------|------|----------------|
| MidnightSatin | `midnightsatin` | `brand-context.midnightsatin.md` |
| Celestial-Nexus | `celestial-nexus` | `brand-context.celestial-nexus.md` |
| HeyBuddy | `heybuddy` | `brand-context.heybuddy.md` |
| tactic.studio | `tactic-studio` | `brand-context.tactic-studio.md` |

Read the per-brand file for product, audience, positioning, and voice. This file holds ranch-wide rules only.

## Shared constraints

- **Never invent proof.** No fabricated stats, testimonials, customer logos, or EIN/tax status. Write `[NEED: x]` for missing proof.
- **No live ad accounts.** Discovery and briefs only — never log into, spend on, or modify ad platforms.
- **No outbound send.** tactic.studio email/DM is gated: Pete (`pete@tactic.studio`) + naming-rights required. Never send.
- **No live SEO deploys.** The SEO agent writes review and plan documents only. Humans implement on the target sites.
- **Verifier scope.** DNS, MX, and HTTP only — no social login scraping (Facebook, LinkedIn, etc.).
- **Spark routing.** All LLM calls go through spark-queue (cap 4 concurrent). Never point agents at SGLang directly.
- **Epistemic honesty.** Label inference as inference. State gaps explicitly.

## Marketing skill

Progressive disclosure: start at `skills/marketing-agi/SKILL.md` (router). Load only the `references/*.md` module needed for the task — do not concatenate all modules into one prompt.
