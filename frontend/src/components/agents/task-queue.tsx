import { QueueLaneCard } from "@/components/agents/queue-lane"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { AgentObserver, QueueLane } from "@/lib/api"
import { cn } from "@/lib/utils"

type TaskQueueRailProps = {
  waiting: number
  lanes: QueueLane[]
  agentsByName: Record<string, AgentObserver>
  onResume: (name: string) => void
  variant?: "sidebar" | "inline"
}

export function TaskQueueRail({
  waiting,
  lanes,
  agentsByName,
  onResume,
  variant = "sidebar",
}: TaskQueueRailProps) {
  const body = (
    <div className={cn("flex flex-col gap-2", variant === "sidebar" && "pr-2")}>
      {lanes.map((lane) => (
        <QueueLaneCard
          key={lane.id}
          lane={lane}
          agent={agentsByName[lane.agent_name] ?? null}
          onResume={onResume}
        />
      ))}
    </div>
  )

  return (
    <div
      className={cn(
        "flex flex-col gap-2",
        variant === "sidebar" && "h-full min-h-0 w-full",
        variant === "inline" && "rounded-[4px] border border-border bg-sidebar p-3",
      )}
    >
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-foreground">Task Queue</h2>
        <span className="font-mono text-[11px] text-blocked">{waiting} waiting</span>
      </div>
      <p className="text-[11px] leading-[1.35] text-muted-foreground">
        Prompt titles waiting to be claimed. Most backlogged first. Empty lanes that should be
        filled get an agent assigned to seed them.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1">
          <span className="size-2 rounded-px bg-blocked" />
          <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            BACKLOGGED
          </span>
        </span>
        <span className="flex items-center gap-1">
          <span className="size-2 rounded-px bg-primary" />
          <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            UNDERSTAFFED
          </span>
        </span>
      </div>
      {variant === "sidebar" ? (
        <ScrollArea className="min-h-0 flex-1">{body}</ScrollArea>
      ) : (
        body
      )}
    </div>
  )
}
