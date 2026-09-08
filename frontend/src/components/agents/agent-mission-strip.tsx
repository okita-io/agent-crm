import { Link } from "react-router-dom"

import { MissionFocusEditor } from "@/components/agents/mission-focus-editor"
import type { Project, ProjectChannelName } from "@/lib/api"
import { CHANNEL_LABELS, channelForAgent } from "@/lib/projects"

const DEFAULT_SLUG = "tactic-studio"

type AgentMissionStripProps = {
  projects: Project[]
  selectedAgentName?: string | null
  selectedAgentLabel?: string | null
  onSave: (slug: string, payload: {
    origin_prompt: string
    channels: Record<string, { armed: boolean; prompt: string }>
  }) => Promise<void>
}

function stripTitle(channel: ProjectChannelName | null, agentLabel: string | null): string {
  if (channel) return `${CHANNEL_LABELS[channel].toUpperCase()} MISSION`
  if (agentLabel) return `${agentLabel.toUpperCase()} MISSION`
  return "HUNT / ENRICHMENT MISSION"
}

export function AgentMissionStrip({
  projects,
  selectedAgentName = null,
  selectedAgentLabel = null,
  onSave,
}: AgentMissionStripProps) {
  const project =
    projects.find((item) => item.slug === DEFAULT_SLUG) ?? projects.find((item) => item.enabled)

  if (!project) return null

  const channel = selectedAgentName ? channelForAgent(selectedAgentName) : null
  const channels: ProjectChannelName[] = channel
    ? [channel]
    : selectedAgentName
      ? []
      : ["hunter", "research"]
  const hunterLike = channel === "hunter" || channel === "research" || !selectedAgentName

  return (
    <section
      id="agent-mission"
      className="flex flex-col gap-2 rounded-[4px] border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            {stripTitle(channel, selectedAgentLabel)}
          </p>
          <p className="text-xs text-muted-foreground">
            {project.name} ·{" "}
            {selectedAgentLabel
              ? `${selectedAgentLabel} prompt`
              : "edit focus without leaving the floor"}
          </p>
        </div>
        <Link
          to={hunterLike ? "/hunter" : "/projects"}
          className="font-mono text-[10px] text-primary hover:underline"
        >
          {hunterLike ? "Open Hunter controls →" : "Open project prompts →"}
        </Link>
      </div>
      <MissionFocusEditor
        project={project}
        channels={channels}
        compact
        onSave={(payload) => onSave(project.slug, payload)}
      />
      {selectedAgentName && !channel ? (
        <p className="font-mono text-[10px] text-faint">
          {selectedAgentLabel || selectedAgentName} has no seeded channel prompt. Pause, skills,
          and resources stay on the card.
        </p>
      ) : null}
    </section>
  )
}
