import { AgentCard } from "@/components/agents/agent-card"
import { LeadChart } from "@/components/agents/lead-chart"
import { SparkSlot, type SparkSlotModel } from "@/components/agents/spark-slot"
import { TaskQueueRail } from "@/components/agents/task-queue"
import { TokenStrip } from "@/components/agents/token-strip"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import type { AgentObserver, CatalogGrowth, SparkSummary } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useFloorContext } from "@/hooks/floor-context"
import { isPlaceholder, isToggleable } from "@/lib/roster"

function sparkSlots(spark: SparkSummary | null, agents: AgentObserver[]): SparkSlotModel[] {
  const max = spark?.max_concurrency ?? 4
  const names = Object.fromEntries(agents.map((agent) => [agent.name, agent.display_name]))
  const model = spark?.model || "spark"
  const slots: SparkSlotModel[] = []
  for (const actor of spark?.in_flight ?? []) {
    slots.push({
      title: names[actor] || actor,
      subtitle: `${model} · CRM`,
      state: "IN FLIGHT",
    })
  }
  for (let index = 0; index < (spark?.external_upstream_slots ?? 0); index += 1) {
    slots.push({
      title: "External / Hermes",
      subtitle: `${model} · upstream`,
      state: "EXTERNAL",
    })
  }
  const waiters = spark?.waiters ?? []
  while (slots.length < max) {
    const waiter = waiters[slots.length - (spark?.in_flight.length ?? 0)] || waiters[0]
    slots.push({
      title: "Open slot",
      subtitle: waiter ? `waiting: ${names[waiter] || waiter}` : `${model} · free`,
      state: waiter ? "WAITING" : "FREE",
    })
  }
  return slots.slice(0, max)
}

function growthBars(growth: CatalogGrowth | null, metric: string): number[] {
  if (!growth) return [0, 0, 0]
  return ["1h", "4h", "24h"].map((window) => growth.per_hour[window]?.[metric] ?? 0)
}

function growthDelta(growth: CatalogGrowth | null, metric: string): { text: string; up: boolean } {
  if (!growth) return { text: "—", up: false }
  const recent = growth.per_hour["1h"]?.[metric] ?? 0
  const baseline = growth.per_hour["24h"]?.[metric] ?? 0
  const diff = recent - baseline
  if (baseline === 0 && recent === 0) return { text: "flat vs 24h pace", up: false }
  const sign = diff >= 0 ? "+" : ""
  return {
    text: `${sign}${diff.toFixed(1)} /hr vs 24h`,
    up: diff >= 0,
  }
}

export function LiveAgentsPage() {
  const floor = useFloorContext()
  const staffed = floor.agents.filter((agent) => !isPlaceholder(agent.name, agent.placeholder))
  const unstaffed = floor.agents.filter((agent) => isPlaceholder(agent.name, agent.placeholder))
  const maxSlots = floor.spark?.max_concurrency ?? 4
  const prompt = staffed.reduce((sum, agent) => sum + agent.prompt_tokens, 0)
  const completion = staffed.reduce((sum, agent) => sum + agent.completion_tokens, 0)
  const saved = staffed.reduce((sum, agent) => sum + agent.saved_usd, 0)
  const hourly = staffed.reduce((sum, agent) => sum + agent.tokens_per_hour, 0)
  const emailsHour = floor.growth?.windows["1h"]?.emails ?? 0
  const emailsDay = floor.growth?.windows["24h"]?.emails ?? 0
  const websitesDay = floor.growth?.windows["24h"]?.websites ?? 0
  const hourDelta = growthDelta(floor.growth, "emails")
  const siteDelta = growthDelta(floor.growth, "websites")
  const agentByName = Object.fromEntries(floor.agents.map((agent) => [agent.name, agent]))

  async function pauseAll() {
    const targets = floor.agents.filter(
      (agent) => isToggleable(agent.name, agent.toggleable) && agent.enabled,
    )
    await Promise.all(targets.map((agent) => floor.setEnabled(agent.name, false)))
  }

  return (
    <div className="flex min-h-0 flex-1">
      <ScrollArea className="min-w-0 flex-1">
        <div className="flex flex-col gap-3 p-4 lg:p-[18px]">
          <header className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-[22px] font-semibold text-foreground">Live Agents</h1>
              <p className="text-xs text-muted-foreground">
                Floor view · task queue · {maxSlots} concurrent Spark slots
              </p>
            </div>
            <div className="flex items-center gap-2.5">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-working-dim px-2.5 py-1.5">
                <span className={cn("size-[7px] rounded-full bg-working", floor.error && "bg-blocked")} />
                <span className={cn("font-mono text-[10px] font-semibold", floor.error ? "text-blocked" : "text-working")}>
                  {floor.error ? "OFFLINE" : "LIVE 5s"}
                </span>
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="rounded-[4px] text-xs text-muted-foreground"
                onClick={() => void pauseAll()}
              >
                Pause all
              </Button>
            </div>
          </header>

          {floor.error ? (
            <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
              {floor.error}
            </p>
          ) : null}

          <div className="flex items-center justify-between">
            <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
              CONCURRENT SLOTS
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">
              spark-queue · {floor.spark?.model || "offline"} · {floor.spark?.local_in_flight ?? 0} in-flight ·{" "}
              {floor.spark?.waiting ?? 0} waiting
            </span>
          </div>

          <div className="flex gap-2">
            {sparkSlots(floor.spark, floor.agents).map((slot, index) => (
              <SparkSlot key={index} index={index} slot={slot} />
            ))}
          </div>

          <div className="flex gap-2">
            <LeadChart
              label="NEW LEADS / HOUR"
              value={String(emailsHour)}
              delta={hourDelta.text}
              deltaPositive={hourDelta.up}
              bars={growthBars(floor.growth, "emails")}
            />
            <LeadChart
              label="NEW LEADS / 24H"
              value={String(emailsDay)}
              delta={`${emailsHour} in the last hour`}
              deltaPositive={emailsHour > 0}
              bars={growthBars(floor.growth, "emails")}
            />
            <LeadChart
              label="NEW SITES / 24H"
              value={String(websitesDay)}
              delta={siteDelta.text}
              deltaPositive={siteDelta.up}
              bars={growthBars(floor.growth, "websites")}
            />
          </div>

          <TokenStrip prompt={prompt} completion={completion} hourly={hourly} saved={saved} />

          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
              UNSTAFFED ROSTER
            </span>
            <div className="flex flex-wrap gap-1.5">
              {unstaffed.map((agent) => (
                <span
                  key={agent.name}
                  className="rounded-[2px] bg-raised px-2 py-0.5 text-[10px] text-faint"
                >
                  {agent.display_name}
                </span>
              ))}
            </div>
          </div>

          {staffed.length ? (
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              {staffed.map((agent) => (
                <AgentCard
                  key={agent.name}
                  agent={agent}
                  catalog={floor.skills}
                  onEnabledChange={(name, enabled) => void floor.setEnabled(name, enabled)}
                  onAssignSkill={(name, skillId) => void floor.assignSkill(name, skillId)}
                  onUnassignSkill={(name, skillId) => void floor.unassignSkill(name, skillId)}
                />
              ))}
            </div>
          ) : (
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-64 rounded-[4px] bg-card" />
              ))}
            </div>
          )}

          <div className="xl:hidden">
            <TaskQueueRail
              waiting={floor.queues?.waiting ?? 0}
              lanes={floor.queues?.lanes ?? []}
              agentsByName={agentByName}
              onResume={(name) => void floor.setEnabled(name, true)}
              variant="inline"
            />
          </div>
        </div>
      </ScrollArea>

      <aside className="hidden h-full min-h-0 w-[332px] shrink-0 flex-col border-l border-sidebar-border bg-sidebar p-3.5 xl:flex">
        <TaskQueueRail
          waiting={floor.queues?.waiting ?? 0}
          lanes={floor.queues?.lanes ?? []}
          agentsByName={agentByName}
          onResume={(name) => void floor.setEnabled(name, true)}
        />
      </aside>
    </div>
  )
}
