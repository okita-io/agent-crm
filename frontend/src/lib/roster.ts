const PLACEHOLDERS = new Set([
  "lead_intake",
  "lead_scoring",
  "outreach_writer",
  "nurture",
  "crm_manager",
  "analytics",
  "brand_router",
  "lead_verifier",
])

const TOGGLEABLE = new Set([
  "research",
  "outbound_hunter",
  "engagement",
  "seo",
  "aeo-geo",
  "queue-review",
  "job-dispatcher",
  "orchestrator",
])

export function isPlaceholder(name: string, flag?: boolean): boolean {
  if (typeof flag === "boolean") return flag
  return PLACEHOLDERS.has(name)
}

export function isToggleable(name: string, flag?: boolean): boolean {
  if (typeof flag === "boolean") return flag
  return TOGGLEABLE.has(name)
}
