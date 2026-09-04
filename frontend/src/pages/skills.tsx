import { FileCode, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useFloorContext } from "@/hooks/floor-context"
import { skillLabel } from "@/lib/agent-meta"

export function SkillsPage() {
  const floor = useFloorContext()
  const packs = floor.skills.filter((skill) => skill.kind === "pack")
  const modules = floor.skills.filter((skill) => skill.kind === "module")

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="flex flex-col gap-4 p-4 lg:p-[18px]">
        <header>
          <h1 className="text-[22px] font-semibold text-foreground">Skills</h1>
          <p className="max-w-xl text-xs text-muted-foreground">
            Vendored packs under <span className="font-mono">skills/</span>. Trash unassigns a skill from
            every agent. Files are never deleted.
          </p>
        </header>

        {floor.error ? (
          <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
            {floor.error}
          </p>
        ) : null}

        <section className="flex flex-col gap-1.5">
          <h2 className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            PACKS
          </h2>
          <div className="flex flex-col gap-1">
            {packs.map((skill) => (
              <SkillRow
                key={skill.id}
                name={skill.label}
                summary={skill.summary}
                agentCount={skill.agent_count}
                onRemove={() => void floor.unassignSkillEverywhere(skill.id)}
              />
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-1.5">
          <h2 className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            MODULES
          </h2>
          <div className="flex flex-col gap-1">
            {modules.map((skill) => (
              <SkillRow
                key={skill.id}
                name={`${skill.pack} / ${skillLabel(skill.id)}`}
                summary={skill.summary}
                agentCount={skill.agent_count}
                onRemove={() => void floor.unassignSkillEverywhere(skill.id)}
              />
            ))}
          </div>
        </section>
      </div>
    </ScrollArea>
  )
}

function SkillRow({
  name,
  summary,
  agentCount,
  onRemove,
}: {
  name: string
  summary: string
  agentCount: number
  onRemove: () => void
}) {
  const agentLabel = agentCount === 1 ? "1 agent" : `${agentCount} agents`

  return (
    <div className="flex items-center gap-3 rounded-[4px] border border-border bg-card px-3 py-2">
      <FileCode className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate font-mono text-[13px] text-foreground">{name}</p>
        <p className="truncate text-[11px] text-muted-foreground">{summary || agentLabel}</p>
      </div>
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{agentLabel}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        className="text-faint hover:text-blocked"
        aria-label={`Unassign ${name} from all agents`}
        onClick={onRemove}
      >
        <Trash2 className="size-3.5" />
      </Button>
    </div>
  )
}
