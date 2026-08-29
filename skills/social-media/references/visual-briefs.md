# Visual briefs

Turn a post package or newsletter section into a graphic **brief** a human can run in ComfyUI (preferred on the Spark host) or paste into any image generator. Adapted from Charlie Hills' graphic-designer, gemini-infographic, and gemini-carousel skills.

This stack does not call Gemini, Midjourney, or ComfyUI APIs. It writes prompts and optional HTML.

## Pick a format

| Content shape | Format |
|---------------|--------|
| Numbered steps, comparison, framework, table | **HTML/CSS graphic** (1200×1400, screenshot to export) |
| Recap of tips, workflow, or a concept | **Infographic** 1080×1350 (whiteboard or branded) |
| One idea per frame, 6–10 frames | **Carousel** 1080×1350 per slide |
| Video cover (later) | YouTube 1280×720 — defer unless a title exists |

The graphic must **recap the post**. It is not an abstract illustration or stock photo.

## Distil first

From the source post, extract:

- Headline (5–10 words)
- 3–7 key points, each ≤10 words
- Numbers that are in brand-context proof only
- Footer: brand name + "human-reviewed" — do not invent follower counts

If there is nothing to recap, say text-only is the right call.

## HTML graphic

Single self-contained HTML file, inline CSS, viewport meta.

- Dark or brand-colour background, high contrast
- Clean sans-serif (system-ui / Inter)
- 40px minimum padding
- One accent colour
- No stock photo backgrounds
- Section count follows the content (3 steps = 3 blocks)

Tell the human: open in a browser and screenshot.

## Infographic prompt (generator-agnostic)

State size **1080×1350**. Two styles:

**Whiteboard:** photograph of a real whiteboard / notebook; marker texture; no digital fonts; imperfect lines.

**Branded:** flat, no gradients required; brand colours from brand-context if present; max ~40 words on the image.

The prompt must be paste-ready. Do not include local file paths. The human attaches a reference photo separately if needed.

## Carousel

Gate on a slide-by-slide brief before prompts:

- Slide 1 cover (hook)
- Body slides: one idea, ≤15 words body
- Last slide: CTA that a human can actually honor (no auto-DM)

Then one prompt per slide, **identical brand style block** on every prompt. 1080×1350, 4:5.

## Output

```
# Visual brief — [brand] — [source post title] — [date]
Format: html | infographic-whiteboard | infographic-branded | carousel
Size: ...

## Distillation
...

## HTML (if format=html)
[file contents or path]

## Prompts
[one code block per image]

## ComfyUI notes
Checkpoint / size / that this is a brief not a rendered asset.

## What I couldn't determine
[missing brand colours, missing proof numbers]
```

## Rules

- Never generate the image in this CRM
- Never put a Charlie Hills "Follow [Name] \| Repost" footer on ranch brands unless Voice asks for a repost CTA
- Skip quote-card / motivational-poster formats unless the brand Voice is explicitly that (none of the four ranch brands default to it)
