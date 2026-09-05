import type { Project, ProjectChannelName } from "@/lib/api"
import { CHANNEL_HINTS, CHANNEL_LABELS, CHANNEL_ORDER, siteLabel } from "@/lib/projects"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"

type ProjectOriginRailProps = {
  project: Project | null
  onChannelChange: (channel: ProjectChannelName, armed: boolean) => void
  onOriginChange: (origin: string) => void
  onOriginBlur: () => void
}

export function ProjectOriginRail({
  project,
  onChannelChange,
  onOriginChange,
  onOriginBlur,
}: ProjectOriginRailProps) {
  if (!project) {
    return (
      <div className="flex h-full min-h-0 w-full flex-col gap-2">
        <h2 className="text-base font-semibold text-foreground">Prompt origin</h2>
        <p className="text-[11px] leading-[1.35] text-muted-foreground">
          Select a project card to edit its origin prompt and per-channel job switches.
        </p>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-foreground">Prompt origin</h2>
        <span className="font-mono text-[11px] text-primary">{project.slug}</span>
      </div>
      <p className="text-[11px] leading-[1.35] text-muted-foreground">
        This origin is prepended to every seeded job for the armed channels. Turning a switch off
        skips that loop for this project only.
      </p>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-2 pr-2">
          <MetaBlock label="SLUG" value={project.slug} />
          <MetaBlock label="SITE" value={siteLabel(project.site)} muted={!project.site} />
          <MetaBlock
            label="CONTEXT"
            value={project.context_file || "—"}
            muted={!project.context_file}
          />
          <MetaBlock label="ALIAS" value={project.alias || "—"} muted={!project.alias} />

          <div className="rounded-[4px] border border-primary bg-card p-2.5">
            <p className="mb-1.5 font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
              ORIGIN PROMPT
            </p>
            <Textarea
              value={project.origin_prompt}
              onChange={(event) => onOriginChange(event.target.value)}
              onBlur={onOriginBlur}
              className="min-h-[120px] border-transparent bg-transparent px-0 py-0"
            />
          </div>

          <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            JOB SWITCHES
          </p>
          <div className="flex flex-col gap-1.5">
            {CHANNEL_ORDER.map((channel) => {
              const armed = project.channels[channel]?.armed ?? false
              return (
                <label
                  key={channel}
                  className="flex items-center gap-2 rounded-[2px] border border-border bg-card px-2.5 py-2"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-[12px] font-semibold text-foreground">
                      {CHANNEL_LABELS[channel]}
                    </span>
                    <span className="block font-mono text-[10px] text-muted-foreground">
                      {CHANNEL_HINTS[channel]}
                    </span>
                  </span>
                  <Switch
                    size="sm"
                    checked={armed && project.enabled}
                    disabled={!project.enabled}
                    onCheckedChange={(value) => onChannelChange(channel, value)}
                  />
                </label>
              )
            })}
          </div>

          <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            THIS ORIGIN WOULD SEED
          </p>
          <div className="rounded-[4px] border border-border bg-card p-2.5">
            {project.seeded_loops.length ? (
              <div className="flex flex-col gap-1">
                {project.seeded_loops.map((loop) => (
                  <p key={loop} className="font-mono text-[10px] text-primary">
                    {loop}
                  </p>
                ))}
              </div>
            ) : (
              <p className="font-mono text-[10px] text-faint">no armed channels</p>
            )}
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}

function MetaBlock({
  label,
  value,
  muted,
}: {
  label: string
  value: string
  muted?: boolean
}) {
  return (
    <div className="rounded-[4px] border border-border bg-card px-2.5 py-2">
      <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
        {label}
      </p>
      <p className={`mt-0.5 font-mono text-[12px] ${muted ? "text-faint" : "text-foreground"}`}>
        {value}
      </p>
    </div>
  )
}
