import { Expand, Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { AgentObserver, QueueLane } from "@/lib/api"
import { formatWait } from "@/lib/format"
import { isToggleable } from "@/lib/roster"
import { cn } from "@/lib/utils"

type QueueLaneCardProps = {
  lane: QueueLane
  agent: AgentObserver | null
  onResume?: (name: string) => void
  onInspect?: (lane: QueueLane) => void
}

function badgeTone(lane: QueueLane, enabled: boolean) {
  if (lane.pending === 0) return "bg-gold-dim text-primary"
  if (!enabled) return "bg-gold-dim text-primary"
  if (lane.pending >= 8) return "bg-blocked-dim text-blocked"
  if (lane.id === "queue-review") return "bg-thinking-dim text-thinking"
  return "bg-working-dim text-working"
}

function staffLine(lane: QueueLane, agent: AgentObserver | null, enabled: boolean) {
  if (!agent) return "unstaffed"
  const parts = [enabled ? "staffed" : "paused", agent.display_name]
  if (!enabled) {
    return parts.join(" · ")
  }
  if (agent.status === "blocked") {
    parts.push("blocked on Spark")
  } else if (lane.oldest_wait_seconds != null && lane.pending > 0) {
    parts.push(formatWait(lane.oldest_wait_seconds))
  } else if ((lane.running ?? 0) > 0) {
    parts.push(`${lane.running} in flight`)
  }
  return parts.join(" · ")
}

export function QueueLaneCard({ lane, agent, onResume, onInspect }: QueueLaneCardProps) {
  const enabled = agent?.enabled ?? true
  const empty = lane.pending === 0
  const canResume = Boolean(
    agent && !enabled && isToggleable(agent.name, agent.toggleable) && onResume,
  )
  const prompts = lane.prompts.slice(0, 4)
  const extra = Math.max(0, lane.pending - prompts.length)

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-[4px] border border-border bg-card p-2.5",
        onInspect && "cursor-pointer hover:border-primary/50",
      )}
      onClick={onInspect ? () => onInspect(lane) : undefined}
      onKeyDown={
        onInspect
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                onInspect(lane)
              }
            }
          : undefined
      }
      role={onInspect ? "button" : undefined}
      tabIndex={onInspect ? 0 : undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] font-semibold text-foreground">{lane.name}</p>
        <span
          className={cn(
            "rounded-[2px] px-1.5 py-0.5 font-mono text-[11px] font-medium",
            badgeTone(lane, enabled),
          )}
        >
          {lane.pending}
        </span>
      </div>
      <p className="font-mono text-[10px] text-muted-foreground">
        {empty && (!agent || !enabled)
          ? "empty · should be filled"
          : staffLine(lane, agent, enabled)}
      </p>
      <div className="flex flex-col gap-1">
        {prompts.length ? (
          prompts.map((prompt, index) => (
            <p
              key={`${lane.id}-${index}-${prompt}`}
              className={cn(
                "line-clamp-2 font-mono text-[11px] leading-[1.35]",
                index >= 2 ? "text-faint" : "text-foreground",
              )}
            >
              {prompt}
            </p>
          ))
        ) : (
          <p className="font-mono text-[11px] text-faint">—</p>
        )}
        {onInspect ? (
          <p className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
            <Expand className="size-3" />
            {extra > 0 ? `+${extra} more · inspect` : "inspect queue"}
          </p>
        ) : extra > 0 ? (
          <p className="font-mono text-[10px] text-muted-foreground">+{extra} more</p>
        ) : null}
      </div>
      {canResume ? (
        <Button
          type="button"
          size="sm"
          className="mt-0.5 h-7 rounded-[2px] text-[11px]"
          onClick={(event) => {
            event.stopPropagation()
            onResume?.(agent!.name)
          }}
        >
          <Plus className="size-3" />
          {empty ? "Assign agent" : "Resume agent"}
        </Button>
      ) : null}
    </div>
  )
}
