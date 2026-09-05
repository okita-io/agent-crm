import { useCallback, useEffect, useState } from "react"

import {
  api,
  type Project,
  type ProjectChannelName,
  type ProjectStats,
} from "@/lib/api"

const emptyStats: ProjectStats = {
  projects: 0,
  live_sites: 0,
  pre_launch: 0,
  channels_armed: 0,
  channels_total: 0,
}

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([])
  const [stats, setStats] = useState<ProjectStats>(emptyStats)
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const payload = await api.projects()
      setProjects(payload.projects)
      setStats(payload.stats)
      setError(null)
      setSelectedSlug((prev) => {
        if (prev && payload.projects.some((project) => project.slug === prev)) {
          return prev
        }
        return payload.projects[0]?.slug ?? null
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load projects")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const selected = projects.find((project) => project.slug === selectedSlug) ?? null

  const replace = useCallback((project: Project) => {
    setProjects((prev) => prev.map((item) => (item.slug === project.slug ? project : item)))
    setStats((prev) => {
      const next = prev
      return next
    })
    void load()
    return project
  }, [load])

  const setEnabled = useCallback(
    async (slug: string, enabled: boolean) => {
      setProjects((prev) =>
        prev.map((project) => (project.slug === slug ? { ...project, enabled } : project)),
      )
      try {
        const project = await api.patchProject(slug, { enabled })
        replace(project)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not update project")
        await load()
      }
    },
    [load, replace],
  )

  const setChannel = useCallback(
    async (slug: string, channel: ProjectChannelName, armed: boolean) => {
      setProjects((prev) =>
        prev.map((project) => {
          if (project.slug !== slug) return project
          return {
            ...project,
            channels: {
              ...project.channels,
              [channel]: { ...project.channels[channel], armed },
            },
          }
        }),
      )
      try {
        const project = await api.putProjectChannels(slug, { [channel]: armed })
        replace(project)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not update channel")
        await load()
      }
    },
    [load, replace],
  )

  const patch = useCallback(
    async (
      slug: string,
      body: Partial<{
        name: string
        site: string | null
        alias: string | null
        status: string
        origin_prompt: string
        enabled: boolean
      }>,
    ) => {
      const project = await api.patchProject(slug, body)
      replace(project)
      return project
    },
    [replace],
  )

  const savePrompts = useCallback(
    async (
      slug: string,
      body: { origin_prompt?: string; channels?: Partial<Record<ProjectChannelName, string>> },
    ) => {
      const project = await api.putProjectPrompts(slug, body)
      replace(project)
      return project
    },
    [replace],
  )

  const create = useCallback(
    async (body: {
      slug: string
      name: string
      site?: string | null
      origin_prompt?: string
      alias?: string | null
    }) => {
      const project = await api.createProject(body)
      await load()
      setSelectedSlug(project.slug)
      return project
    },
    [load],
  )

  const reloadContext = useCallback(
    async (slug: string) => {
      const project = await api.reloadProjectContext(slug)
      replace(project)
      return project
    },
    [replace],
  )

  const saveSettings = useCallback(
    async (
      slug: string,
      payload: {
        origin_prompt: string
        channels: Record<ProjectChannelName, { armed: boolean; prompt: string }>
      },
    ) => {
      const armed: Partial<Record<ProjectChannelName, boolean>> = {}
      const prompts: Partial<Record<ProjectChannelName, string>> = {}
      for (const [name, channel] of Object.entries(payload.channels) as [
        ProjectChannelName,
        { armed: boolean; prompt: string },
      ][]) {
        armed[name] = channel.armed
        prompts[name] = channel.prompt
      }
      await api.putProjectChannels(slug, armed)
      const project = await api.putProjectPrompts(slug, {
        origin_prompt: payload.origin_prompt,
        channels: prompts,
      })
      replace(project)
      return project
    },
    [replace],
  )

  return {
    projects,
    stats,
    selected,
    selectedSlug,
    setSelectedSlug,
    error,
    loading,
    load,
    setEnabled,
    setChannel,
    patch,
    savePrompts,
    saveSettings,
    create,
    reloadContext,
  }
}
