import { Link } from "react-router-dom"

import { MissionFocusEditor } from "@/components/agents/mission-focus-editor"
import type { Project } from "@/lib/api"

const DEFAULT_SLUG = "tactic-studio"

type AgentMissionStripProps = {
  projects: Project[]
  onSave: (slug: string, payload: {
    origin_prompt: string
    channels: Record<string, { armed: boolean; prompt: string }>
  }) => Promise<void>
}

export function AgentMissionStrip({ projects, onSave }: AgentMissionStripProps) {
  const project =
    projects.find((item) => item.slug === DEFAULT_SLUG) ?? projects.find((item) => item.enabled)

  if (!project) return null

  return (
    <section className="flex flex-col gap-2 rounded-[4px] border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            HUNT / ENRICHMENT MISSION
          </p>
          <p className="text-xs text-muted-foreground">
            {project.name} · edit focus without leaving the floor
          </p>
        </div>
        <Link
          to="/hunter"
          className="font-mono text-[10px] text-primary hover:underline"
        >
          Open Hunter controls →
        </Link>
      </div>
      <MissionFocusEditor
        project={project}
        channels={["hunter", "research"]}
        compact
        onSave={(payload) => onSave(project.slug, payload)}
      />
    </section>
  )
}
