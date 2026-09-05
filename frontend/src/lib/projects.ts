export const CHANNEL_ORDER = [
  "research",
  "hunter",
  "seo",
  "aeo_geo",
  "engage",
  "publish",
] as const

export const CHANNEL_LABELS: Record<(typeof CHANNEL_ORDER)[number], string> = {
  research: "Research",
  hunter: "Hunter",
  seo: "SEO",
  aeo_geo: "AEO / GEO",
  engage: "Engage",
  publish: "Publish",
}

export const CHANNEL_HINTS: Record<(typeof CHANNEL_ORDER)[number], string> = {
  research: "seeds research_queries",
  hunter: "seeds hunt_queries",
  seo: "owned seo targets",
  aeo_geo: "aeo-geo reviews",
  engage: "engagement_queries",
  publish: "publish_jobs",
}

export function statusTone(status: string) {
  switch (status) {
    case "pre_launch":
      return { label: "PRE-LAUNCH", text: "text-thinking", fill: "bg-thinking" }
    case "renamed":
      return { label: "RENAMED", text: "text-primary", fill: "bg-primary" }
    case "paused":
      return { label: "PAUSED", text: "text-faint", fill: "bg-faint" }
    default:
      return { label: "LIVE", text: "text-working", fill: "bg-working" }
  }
}

export function siteLabel(site: string | null) {
  if (!site) return "— no site yet"
  try {
    return new URL(site).host
  } catch {
    return site.replace(/^https?:\/\//, "")
  }
}
