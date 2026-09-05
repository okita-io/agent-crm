import { Settings } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import type { Project, ProjectChannelName } from "@/lib/api"
import { CHANNEL_LABELS, CHANNEL_ORDER, siteLabel, statusTone } from "@/lib/projects"
import { cn } from "@/lib/utils"

type ProjectCardProps = {
  project: Project
  selected: boolean
  onSelect: () => void
  onEnabledChange: (enabled: boolean) => void
  onChannelChange: (channel: ProjectChannelName, armed: boolean) => void
  onOpenSettings: () => void
}

export function ProjectCard({
  project,
  selected,
  onSelect,
  onEnabledChange,
  onChannelChange,
  onOpenSettings,
}: ProjectCardProps) {
  const tone = statusTone(project.status)

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onSelect()
        }
      }}
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-2.5 rounded-[4px] border bg-card p-3.5 text-left",
        selected ? "border-primary" : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn("size-2 shrink-0 rounded-full", tone.fill)} />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">{project.name}</p>
            <p className={cn("font-mono text-[10px] font-semibold tracking-[0.6px]", tone.text)}>
              {tone.label}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5" onClick={(event) => event.stopPropagation()}>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            className="text-muted-foreground"
            aria-label={`${project.name} settings`}
            onClick={onOpenSettings}
          >
            <Settings className="size-[13px]" />
          </Button>
          <Switch
            size="sm"
            checked={project.enabled}
            onCheckedChange={onEnabledChange}
            aria-label={`Enable ${project.name}`}
          />
        </div>
      </div>

      <div>
        <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          SITE
        </p>
        <p
          className={cn(
            "mt-0.5 truncate font-mono text-[11px]",
            project.site ? "text-foreground" : "text-faint",
          )}
        >
          {siteLabel(project.site)}
        </p>
      </div>

      <div>
        <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          PROMPT ORIGIN
        </p>
        <p className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-foreground">
          {project.summary || project.origin_prompt || "No origin prompt yet"}
        </p>
      </div>

      <div className="mt-auto">
        <p className="mb-1.5 font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          JOB CHANNELS
        </p>
        <div className="grid grid-cols-2 gap-1.5" onClick={(event) => event.stopPropagation()}>
          {CHANNEL_ORDER.map((channel) => {
            const armed = project.channels[channel]?.armed ?? false
            return (
              <label
                key={channel}
                className="flex items-center justify-between gap-2 rounded-[2px] bg-raised/50 px-2 py-1"
              >
                <span className="font-mono text-[10px] text-muted-foreground">
                  {CHANNEL_LABELS[channel]}
                </span>
                <Switch
                  size="sm"
                  checked={armed && project.enabled}
                  disabled={!project.enabled}
                  onCheckedChange={(value) => onChannelChange(channel, value)}
                  aria-label={`${project.name} ${CHANNEL_LABELS[channel]}`}
                />
              </label>
            )
          })}
        </div>
      </div>

      <div>
        <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          WIRES INTO
        </p>
        <p className="mt-0.5 font-mono text-[10px] text-faint">
          {project.seeded_loops.length ? project.seeded_loops.join(" · ") : "none armed"}
        </p>
      </div>
    </article>
  )
}
