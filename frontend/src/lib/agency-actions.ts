export type AgencyAction = {
  type?: string
  ok?: boolean
  agent?: string
  enabled?: boolean
  query?: string
  brand?: string
  kind?: string
  skill_id?: string
  detail?: string
  [key: string]: unknown
}

function isAction(value: unknown): value is AgencyAction {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

export function unpackAgencyActions(raw: unknown): {
  planned: AgencyAction[]
  results: AgencyAction[]
} {
  if (!raw) return { planned: [], results: [] }
  if (Array.isArray(raw) && raw.length === 1 && isAction(raw[0]) && "planned" in raw[0]) {
    const wrap = raw[0]
    const planned = Array.isArray(wrap.planned) ? wrap.planned.filter(isAction) : []
    const results = Array.isArray(wrap.results) ? wrap.results.filter(isAction) : []
    return { planned, results }
  }
  if (Array.isArray(raw)) return { planned: raw.filter(isAction), results: [] }
  return { planned: [], results: [] }
}

export function describeAgencyAction(action: AgencyAction): string {
  const type = String(action.type || "action")
  if (type === "set_agent_enabled") {
    const verb = action.enabled ? "Enable" : "Pause"
    return `${verb} ${action.agent ?? "agent"}`
  }
  if (type === "assign_skill") {
    return `Assign ${action.skill_id ?? "skill"} → ${action.agent ?? "agent"}`
  }
  if (type === "unassign_skill") {
    return `Unassign ${action.skill_id ?? "skill"} ← ${action.agent ?? "agent"}`
  }
  if (type.startsWith("enqueue_")) {
    const lane = type.replace("enqueue_", "").replace(/_/g, " ")
    const brand = action.brand ? ` · ${action.brand}` : ""
    return `Enqueue ${lane}${brand}`
  }
  return type.replace(/_/g, " ")
}
