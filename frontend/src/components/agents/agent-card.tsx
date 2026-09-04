import { Settings } from "lucide-react"

import { AddSkillPopover } from "@/components/agents/add-skill-popover"
import { ResourceChip } from "@/components/agents/resource-chip"
import { SkillTag } from "@/components/agents/skill-tag"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import type { AgentObserver, SkillCatalogItem } from "@/lib/api"
import { metaFor, skillLabel, skillsFor, statusTone } from "@/lib/agent-meta"
import { formatTokenCount, formatTokenRate } from "@/lib/format"
import { isToggleable } from "@/lib/roster"
import { cn } from "@/lib/utils"

type AgentCardProps = {
  agent: AgentObserver
  catalog: SkillCatalogItem[]
  onEnabledChange: (name: string, enabled: boolean) => void
  onAssignSkill: (name: string, skillId: string) => void
  onUnassignSkill: (name: string, skillId: string) => void
}

export function AgentCard({
  agent,
  catalog,
  onEnabledChange,
  onAssignSkill,
  onUnassignSkill,
}: AgentCardProps) {
  const tone = statusTone(agent.status, agent.enabled)
  const meta = metaFor(agent.name)
  const canToggle = isToggleable(agent.name, agent.toggleable)
  const skills = skillsFor(agent)

  return (
    <article
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-3 rounded-[4px] border bg-card p-3.5",
        tone.border,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn("size-2 shrink-0 rounded-full", tone.fill)} />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">{agent.display_name}</p>
            <p className={cn("font-mono text-[10px] font-semibold tracking-[0.6px]", tone.text)}>
              {tone.label}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <AgentSettingsSheet
            agent={agent}
            catalog={catalog}
            skills={skills}
            onAssignSkill={onAssignSkill}
            onUnassignSkill={onUnassignSkill}
          />
          <Switch
            size="sm"
            checked={agent.enabled}
            disabled={!canToggle}
            onCheckedChange={(enabled) => onEnabledChange(agent.name, enabled)}
            aria-label={`Enable ${agent.display_name}`}
          />
        </div>
      </div>

      <div>
        <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">TASK</p>
        <p className="mt-0.5 line-clamp-2 text-[12px] text-foreground">
          {agent.task || "Waiting for work"}
        </p>
      </div>

      <div>
        <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">FILES</p>
        <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
          {agent.resource || "—"}
        </p>
      </div>

      <div>
        <p className="mb-1 font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          RESOURCES
        </p>
        {meta.resources.length ? (
          <div className="flex flex-wrap gap-1.5">
            {meta.resources.map((resource) => (
              <ResourceChip key={resource.label} icon={resource.icon} label={resource.label} />
            ))}
          </div>
        ) : (
          <span className="text-[11px] text-faint">None</span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        <TokenCol label="IN" value={formatTokenCount(agent.prompt_tokens)} />
        <TokenCol label="OUT" value={formatTokenCount(agent.completion_tokens)} />
        <TokenCol label="RATE" value={formatTokenRate(agent.tokens_per_hour)} />
      </div>

      <div className="mt-auto">
        <div className="mb-1.5 flex items-center justify-between">
          <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            SKILLS
          </p>
          <AddSkillPopover
            assigned={skills}
            catalog={catalog}
            onAssign={(skillId) => onAssignSkill(agent.name, skillId)}
          />
        </div>
        {skills.length ? (
          <div className="flex flex-wrap gap-1.5">
            {skills.map((skill) => (
              <SkillTag
                key={skill}
                label={skillLabel(skill)}
                onRemove={() => onUnassignSkill(agent.name, skill)}
              />
            ))}
          </div>
        ) : (
          <span className="text-[11px] text-faint">No skills</span>
        )}
      </div>
    </article>
  )
}

function TokenCol({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">{label}</p>
      <p className="font-mono text-[12px] font-semibold text-foreground">{value}</p>
    </div>
  )
}

function AgentSettingsSheet({
  agent,
  catalog,
  skills,
  onAssignSkill,
  onUnassignSkill,
}: {
  agent: AgentObserver
  catalog: SkillCatalogItem[]
  skills: string[]
  onAssignSkill: (name: string, skillId: string) => void
  onUnassignSkill: (name: string, skillId: string) => void
}) {
  const assigned = new Set(skills)
  const packs = catalog.filter((item) => item.kind === "pack")

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="text-muted-foreground"
          aria-label={`${agent.display_name} skill settings`}
        >
          <Settings className="size-[13px]" />
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="bg-card sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{agent.display_name}</SheetTitle>
          <SheetDescription>
            Assign vendored skill packs. Unchecking removes the pack from this agent; files stay on disk.
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-3 px-4 pb-4">
          {packs.map((pack) => {
            const checked = assigned.has(pack.id)
            return (
              <label
                key={pack.id}
                className="flex items-start gap-2 rounded-[4px] border border-border bg-raised/40 px-3 py-2"
              >
                <input
                  type="checkbox"
                  className="mt-1 size-3.5 accent-primary"
                  checked={checked}
                  onChange={() =>
                    checked
                      ? onUnassignSkill(agent.name, pack.id)
                      : onAssignSkill(agent.name, pack.id)
                  }
                />
                <span>
                  <span className="block font-mono text-[12px] text-primary">{pack.label}</span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">
                    {pack.summary || pack.kind}
                  </span>
                </span>
              </label>
            )
          })}
        </div>
      </SheetContent>
    </Sheet>
  )
}
