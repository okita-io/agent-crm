import { CommandComposer } from "@/components/command/command-composer"
import { CommandThread } from "@/components/command/command-thread"
import { OrchestratorRail } from "@/components/command/orchestrator-rail"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { useCommands } from "@/hooks/use-commands"
import { useFloorContext } from "@/hooks/floor-context"
import { statusTone } from "@/lib/agent-meta"
import { isToggleable } from "@/lib/roster"
import { cn } from "@/lib/utils"

function suggestedCommands(hunterEnabled: boolean, orchestratorEnabled: boolean): string[] {
  if (!orchestratorEnabled) return []
  return [
    hunterEnabled ? "Pause hunter" : "Resume hunter",
    "Pause all",
    "Resume all",
    "Hunt tactic-studio for XR brand partnerships",
    "Pause research",
  ]
}

export function CommandPage() {
  const floor = useFloorContext()
  const commands = useCommands()
  const orchestrator = floor.agents.find((agent) => agent.name === "orchestrator") ?? null
  const hunter = floor.agents.find((agent) => agent.name === "outbound_hunter") ?? null
  const tone = statusTone(orchestrator?.status ?? "idle", orchestrator?.enabled ?? true)
  const sparkBlocked = orchestrator?.status === "blocked"
  const chips = suggestedCommands(hunter?.enabled ?? true, orchestrator?.enabled ?? true)
  const canToggleOrchestrator = isToggleable("orchestrator", orchestrator?.toggleable)

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 lg:px-[18px]">
          <div>
            <h1 className="text-[22px] font-semibold text-foreground">Command</h1>
            <p className="text-xs text-muted-foreground">
              Natural-language instructions for the orchestrator
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5", tone.card)}>
              <span className={cn("size-[7px] rounded-full", tone.fill)} />
              <span className={cn("font-mono text-[10px] font-semibold", tone.text)}>{tone.label}</span>
            </span>
            <div className="xl:hidden">
              <Switch
                size="sm"
                checked={orchestrator?.enabled ?? true}
                disabled={!canToggleOrchestrator}
                onCheckedChange={(enabled) => void floor.setEnabled("orchestrator", enabled)}
                aria-label="Enable orchestrator"
              />
            </div>
          </div>
        </header>

        {floor.error || commands.error ? (
          <p className="mx-4 mt-3 shrink-0 rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked lg:mx-[18px]">
            {commands.error || floor.error}
          </p>
        ) : null}

        {chips.length ? (
          <div className="flex shrink-0 flex-wrap gap-1 border-b border-border px-4 py-2.5 lg:px-[18px]">
            {chips.map((chip) => (
              <Button
                key={chip}
                type="button"
                size="xs"
                variant="outline"
                className="rounded-[2px]"
                disabled={commands.sending}
                onClick={() => void commands.send(chip)}
              >
                {chip}
              </Button>
            ))}
          </div>
        ) : (
          <div className="shrink-0 border-b border-border px-4 py-2.5 lg:px-[18px]">
            <p className="text-[11px] text-muted-foreground">
              Orchestrator is paused. Resume it, then send commands.
            </p>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          <CommandThread requests={commands.requests} />
        </div>

        <CommandComposer
          sending={commands.sending}
          onSend={(message) => commands.send(message)}
        />
      </div>

      <aside className="hidden min-h-0 w-[332px] shrink-0 flex-col overflow-hidden border-l border-sidebar-border bg-sidebar p-3.5 xl:flex">
        <OrchestratorRail
          orchestrator={orchestrator}
          sparkModel={floor.spark?.model ?? null}
          sparkBlocked={sparkBlocked}
          lanes={floor.queues?.lanes ?? []}
          onEnabledChange={(enabled) => void floor.setEnabled("orchestrator", enabled)}
        />
      </aside>
    </div>
  )
}
