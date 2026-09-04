import type { AgentStatus } from "@/lib/api"

export type ResourceKind = "globe" | "flame" | "cpu" | "database" | "search"

export type AgentMeta = {
  skills: string[]
  resources: { icon: ResourceKind; label: string }[]
}

const searxng = { icon: "globe" as const, label: "SearXNG" }
const firecrawl = { icon: "flame" as const, label: "Firecrawl" }
const spark = { icon: "cpu" as const, label: "Spark" }
const postgres = { icon: "database" as const, label: "Postgres" }

export const AGENT_META: Record<string, AgentMeta> = {
  research: {
    skills: ["marketing-agi", "positioning", "competitive"],
    resources: [searxng, firecrawl, spark],
  },
  outbound_hunter: {
    skills: ["marketing-agi", "outbound"],
    resources: [searxng, firecrawl, spark],
  },
  engagement: {
    skills: ["social-media", "marketing-agi", "voice"],
    resources: [searxng, firecrawl, spark],
  },
  seo: {
    skills: ["open-seo"],
    resources: [searxng, firecrawl, spark],
  },
  "aeo-geo": {
    skills: ["aeo-geo", "open-seo"],
    resources: [searxng, firecrawl, spark],
  },
  "queue-review": {
    skills: ["marketing-agi"],
    resources: [spark, postgres],
  },
  "job-dispatcher": {
    skills: [],
    resources: [postgres],
  },
  orchestrator: {
    skills: [],
    resources: [spark, postgres],
  },
}

export function metaFor(name: string): AgentMeta {
  return AGENT_META[name] ?? { skills: [], resources: [] }
}

export function skillLabel(skillId: string): string {
  const slash = skillId.lastIndexOf("/")
  return slash >= 0 ? skillId.slice(slash + 1) : skillId
}

export function skillsFor(agent: { name: string; skills?: string[] }): string[] {
  return agent.skills ?? metaFor(agent.name).skills
}

export function statusTone(status: AgentStatus, enabled: boolean) {
  if (!enabled) {
    return {
      label: "PAUSED",
      text: "text-idle",
      fill: "bg-idle",
      border: "border-border",
      card: "bg-card",
    }
  }
  switch (status) {
    case "working":
      return {
        label: "WORKING",
        text: "text-working",
        fill: "bg-working",
        border: "border-working",
        card: "bg-working-dim",
      }
    case "thinking":
      return {
        label: "THINKING",
        text: "text-thinking",
        fill: "bg-thinking",
        border: "border-thinking",
        card: "bg-thinking-dim",
      }
    case "blocked":
      return {
        label: "BLOCKED",
        text: "text-blocked",
        fill: "bg-blocked",
        border: "border-blocked",
        card: "bg-blocked-dim",
      }
    default:
      return {
        label: "IDLE",
        text: "text-idle",
        fill: "bg-idle",
        border: "border-border",
        card: "bg-card",
      }
  }
}
