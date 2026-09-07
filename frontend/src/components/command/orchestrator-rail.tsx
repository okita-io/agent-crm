import { Link } from "react-router-dom"

import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import type { AgentObserver, QueueLane } from "@/lib/api"
import { statusTone } from "@/lib/agent-meta"
import { formatAgo } from "@/lib/format"
import { isToggleable } from "@/lib/roster"
import { cn } from "@/lib/utils"

type OrchestratorRailProps = {
  orchestrator: AgentObserver | null
  sparkModel: string | null
  sparkBlocked: boolean
  lanes: QueueLane[]
  onEnabledChange: (enabled: boolean) => void
}

const CAPABILITIES = [
  "Pause or resume standing agents without Spark",
  "Enqueue hunt / research / engagement / SEO / AEO work (Spark)",
  "Assign or unassign skills",
  "Answer questions about the floor without changing it",
]

export function OrchestratorRail({
  orchestrator,
  sparkModel,
  sparkBlocked,
  lanes,
  onEnabledChange,
}: OrchestratorRailProps) {
  const enabled = orchestrator?.enabled ?? true
  const tone = statusTone(orchestrator?.status ?? "idle", enabled)
  const canToggle = isToggleable("orchestrator", orchestrator?.toggleable)

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div>
        <h2 className="text-base font-semibold text-foreground">Orchestrator</h2>
        <p className="mt-1 text-[11px] leading-[1.35] text-muted-foreground">
          Interprets Command messages. Pause/resume is rule-based; enqueue and skill edits go
          through Spark.
        </p>
      </div>

      <div className={cn("rounded-[4px] border bg-card p-3", tone.border)}>
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className={cn("size-2 shrink-0 rounded-full", tone.fill)} />
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">
                {orchestrator?.display_name ?? "Orchestrator"}
              </p>
              <p className={cn("font-mono text-[10px] font-semibold tracking-[0.6px]", tone.text)}>
                {tone.label}
              </p>
            </div>
          </div>
          <Switch
            size="sm"
            checked={enabled}
            disabled={!canToggle}
            onCheckedChange={onEnabledChange}
            aria-label="Enable orchestrator"
          />
        </div>
        <dl className="mt-3 flex flex-col gap-2">
          <RailStat label="TASK" value={orchestrator?.task || "Waiting for commands"} />
          <RailStat label="HEARTBEAT" value={formatAgo(orchestrator?.last_heartbeat)} />
          <RailStat label="SPARK" value={sparkModel || "offline"} />
        </dl>
        {sparkBlocked ? (
          <p className="mt-2 rounded-[4px] border border-blocked/40 bg-blocked-dim px-2 py-1.5 font-mono text-[10px] leading-[1.4] text-blocked">
            Spark is blocked. Pause/resume still works; enqueue needs the GPU.
          </p>
        ) : null}
        {!enabled ? (
          <p className="mt-2 rounded-[4px] border border-border bg-raised px-2 py-1.5 font-mono text-[10px] leading-[1.4] text-muted-foreground">
            Paused — queued commands wait here. Toggle back on to drain them.
          </p>
        ) : null}
      </div>

      <div className="flex flex-col gap-1.5">
        <h3 className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          CAN DO
        </h3>
        <ul className="flex flex-col gap-1">
          {CAPABILITIES.map((item) => (
            <li key={item} className="text-[11px] leading-[1.35] text-muted-foreground">
              {item}
            </li>
          ))}
        </ul>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <h3 className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            QUEUES
          </h3>
          <Link
            to="/"
            className="font-mono text-[10px] text-primary hover:underline"
          >
            Live Agents
          </Link>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-1 pr-2">
            {lanes.map((lane) => (
              <div
                key={lane.id}
                className="flex items-center justify-between gap-2 rounded-[4px] border border-border bg-card px-2.5 py-1.5"
              >
                <span className="truncate text-[12px] text-foreground">{lane.name}</span>
                <span className="font-mono text-[11px] text-muted-foreground">{lane.pending}</span>
              </div>
            ))}
          </div>
        </ScrollArea>
      </div>
    </div>
  )
}

function RailStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
        {label}
      </dt>
      <dd className="mt-0.5 line-clamp-2 text-[12px] text-foreground">{value}</dd>
    </div>
  )
}
