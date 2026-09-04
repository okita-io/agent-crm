export type AgentStatus = "idle" | "thinking" | "working" | "blocked"

export type AgentObserver = {
  name: string
  display_name: string
  status: AgentStatus
  task: string | null
  resource: string | null
  last_heartbeat: string | null
  prompt_tokens: number
  completion_tokens: number
  saved_usd: number
  tokens_per_hour: number
  enabled: boolean
  placeholder?: boolean
  toggleable?: boolean
  skills?: string[]
}

export type SkillCatalogItem = {
  id: string
  pack: string
  module: string | null
  label: string
  summary: string
  kind: "pack" | "module" | string
  builtin: boolean
  virtual: boolean
  agent_count: number
  agents: string[]
}

export type SkillsCatalog = {
  skills: SkillCatalogItem[]
}

export type SparkSummary = {
  max_concurrency: number
  observed_upstream_in_flight: number
  local_in_flight: number
  waiting: number
  external_upstream_slots: number
  model: string | null
  waiters: string[]
  in_flight: string[]
  token_usage?: {
    totals?: {
      prompt_tokens?: number
      completion_tokens?: number
      saved_usd?: number
      tokens_per_hour?: number
    }
  } | null
}

export type CatalogGrowth = {
  generated_at: string
  windows: Record<string, Record<string, number>>
  per_hour: Record<string, Record<string, number>>
}

export type QueueLane = {
  id: string
  name: string
  agent_name: string
  pending: number
  prompts: string[]
}

export type Queues = {
  waiting: number
  lanes: QueueLane[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return (await response.json()) as T
}

export const api = {
  agents: () => request<AgentObserver[]>("/agents"),
  spark: () => request<SparkSummary>("/agents/spark"),
  growth: () => request<CatalogGrowth>("/report/growth"),
  queues: () => request<Queues>("/queues"),
  setEnabled: (name: string, enabled: boolean) =>
    request<{ name: string; enabled: boolean }>(
      `/agents/${encodeURIComponent(name)}/enabled`,
      { method: "PUT", body: JSON.stringify({ enabled }) },
    ),
  skills: () => request<SkillsCatalog>("/skills"),
  assignSkill: (name: string, skillId: string) =>
    request<{ name: string; skills: string[] }>(
      `/agents/${encodeURIComponent(name)}/skills`,
      { method: "POST", body: JSON.stringify({ skill_id: skillId }) },
    ),
  unassignSkill: (name: string, skillId: string) =>
    request<{ name: string; skills: string[] }>(
      `/agents/${encodeURIComponent(name)}/skills?skill_id=${encodeURIComponent(skillId)}`,
      { method: "DELETE" },
    ),
  unassignSkillEverywhere: (skillId: string) =>
    request<{ skill_id: string; removed: number }>(
      `/skills/assignments?skill_id=${encodeURIComponent(skillId)}`,
      { method: "DELETE" },
    ),
}
