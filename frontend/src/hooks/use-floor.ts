import { useCallback, useEffect, useState } from "react"

import { api, type AgentObserver, type CatalogGrowth, type Queues, type SkillCatalogItem, type SparkSummary } from "@/lib/api"

const LIVE_MS = 5000

export type FloorState = {
  agents: AgentObserver[]
  spark: SparkSummary | null
  growth: CatalogGrowth | null
  queues: Queues | null
  skills: SkillCatalogItem[]
  error: string | null
  updatedAt: number | null
}

const empty: FloorState = {
  agents: [],
  spark: null,
  growth: null,
  queues: null,
  skills: [],
  error: null,
  updatedAt: null,
}

export function useFloor() {
  const [state, setState] = useState<FloorState>(empty)

  const load = useCallback(async () => {
    try {
      const [agents, spark, growth, queues, skillCatalog] = await Promise.all([
        api.agents(),
        api.spark(),
        api.growth(),
        api.queues().catch(() => ({ waiting: 0, lanes: [] })),
        api.skills().catch(() => ({ skills: [] })),
      ])
      setState({
        agents,
        spark,
        growth,
        queues,
        skills: skillCatalog.skills,
        error: null,
        updatedAt: Date.now(),
      })
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error: error instanceof Error ? error.message : "Could not reach the API",
      }))
    }
  }, [])

  useEffect(() => {
    void load()
    const id = window.setInterval(() => {
      void load()
    }, LIVE_MS)
    return () => window.clearInterval(id)
  }, [load])

  const setEnabled = useCallback(async (name: string, enabled: boolean) => {
    setState((prev) => ({
      ...prev,
      agents: prev.agents.map((agent) =>
        agent.name === name ? { ...agent, enabled } : agent,
      ),
    }))
    try {
      await api.setEnabled(name, enabled)
      await load()
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error: error instanceof Error ? error.message : "Could not update agent",
      }))
      await load()
    }
  }, [load])

  const patchAgentSkills = useCallback((name: string, skills: string[]) => {
    setState((prev) => ({
      ...prev,
      agents: prev.agents.map((agent) =>
        agent.name === name ? { ...agent, skills } : agent,
      ),
    }))
  }, [])

  const assignSkill = useCallback(async (name: string, skillId: string) => {
    setState((prev) => ({
      ...prev,
      agents: prev.agents.map((agent) =>
        agent.name === name && !(agent.skills ?? []).includes(skillId)
          ? { ...agent, skills: [...(agent.skills ?? []), skillId] }
          : agent,
      ),
    }))
    try {
      const result = await api.assignSkill(name, skillId)
      patchAgentSkills(name, result.skills)
      await load()
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error: error instanceof Error ? error.message : "Could not assign skill",
      }))
      await load()
    }
  }, [load, patchAgentSkills])

  const unassignSkill = useCallback(async (name: string, skillId: string) => {
    setState((prev) => ({
      ...prev,
      agents: prev.agents.map((agent) =>
        agent.name === name
          ? { ...agent, skills: (agent.skills ?? []).filter((id) => id !== skillId) }
          : agent,
      ),
    }))
    try {
      const result = await api.unassignSkill(name, skillId)
      patchAgentSkills(name, result.skills)
      await load()
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error: error instanceof Error ? error.message : "Could not unassign skill",
      }))
      await load()
    }
  }, [load, patchAgentSkills])

  const unassignSkillEverywhere = useCallback(async (skillId: string) => {
    try {
      await api.unassignSkillEverywhere(skillId)
      await load()
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error: error instanceof Error ? error.message : "Could not unassign skill",
      }))
      await load()
    }
  }, [load])

  return { ...state, reload: load, setEnabled, assignSkill, unassignSkill, unassignSkillEverywhere }
}
