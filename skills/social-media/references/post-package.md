# Post package

Draft one owned-social post as a stored document. Adapted from Charlie Hills' post-writer plus the first-comment pattern. Craft rules for LinkedIn/X live in marketing-agi `references/social.md` — load that excerpt; do not rewrite it here.

This agent never posts.

## Inputs

- brand-context (required)
- topic, matrix cell, or niche-pulse row
- optional context dump (notes, transcript) labelled untrusted
- platform: linkedin | x | instagram | threads | other

If Voice is still adjective-only, write anyway but label the package **un-voiced**.

## Job

Pick one: **authority**, **engagement**, **distribution**, **conversion**. Two jobs = two packages.

## Write

1. Read Voice + marketing-agi social.md hook / mechanics for the platform
2. Run marketing-agi hooks.md only when the piece is short-form video or paid-adjacent; otherwise use social.md hook families
3. One idea per post
4. First line is the truncation-point hook (LinkedIn ~200 characters)
5. End with a question that has a real answer space, or a claim someone can push against — never "Thoughts?"
6. **Links go in the first comment**, not the body (widely observed reach tax on LinkedIn and X)
7. Run `slop-patterns.md` before delivery
8. Flag every specific number, customer, or result that is not in brand-context proof

## First comment (from pinned-comment, without the persona)

The post delivers the argument. The first comment delivers the resource.

- One to four short lines
- The URL or "comment WORD and a human will send X" — this stack does not auto-DM
- No Charlie Hills loser-meme format
- No engagement-bait ("Agree?") unless Voice already does that

Engagement-loop drafts (replies on other people's threads) use the same helpful-first rule: mention the product only when it answers the post. Do not paste a first-comment CTA onto a forum reply.

## Output

```
# [brand] — [platform] — [date]
Job: [job]
Basis: [matrix cell / pulse row / dump]
Voice: [brand-context | un-voiced]

## Hook (pre-fold)
[exact text]

## Body
[ready to paste, real line breaks]

## First comment
[resource or "none"]

## Alt hooks
1. ...
2. ...

## Visual brief
[none | see visual-briefs]

## Flagged
[NEED: ...]

## What I couldn't determine
```

## Rules

- 150–300 words default on LinkedIn unless Voice says otherwise
- Do not add hashtags unless Voice uses them
- Do not invent a "we lost $X" story to make the hook specific
- tactic.studio: no "DM me for a deck"; conversion CTAs stay human-gated
- MidnightSatin: no Kindle / KU framing
- HeyBuddy: do not call HeyBuddy a nonprofit
