import { Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { QueueLane } from "@/lib/api"
import { cn } from "@/lib/utils"

type QueueLaneCardProps = {
  lane: QueueLane
  staffedName: string | null
  enabled: boolean
}

export function QueueLaneCard({ lane, staffedName, enabled }: QueueLaneCardProps) {
  const backlogged = lane.pending >= 8
  const understaffed = lane.pending > 0 && !enabled
  const empty = lane.pending === 0

  return (
    <div className="flex flex-col gap-2 rounded-[4px] border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] font-semibold text-foreground">{lane.name}</p>
        <span
          className={cn(
            "rounded-[2px] px-1.5 py-0.5 font-mono text-[11px] font-medium",
            backlogged
              ? "bg-blocked-dim text-blocked"
              : understaffed
                ? "bg-gold-dim text-primary"
                : "bg-raised text-muted-foreground",
          )}
        >
          {lane.pending}
        </span>
      </div>
      <p className="font-mono text-[10px] text-muted-foreground">
        {staffedName
          ? `staffed · ${staffedName}${enabled ? "" : " · paused"}`
          : "unstaffed"}
      </p>
      <div className="flex flex-col gap-1">
        {lane.prompts.length ? (
          lane.prompts.slice(0, 3).map((prompt) => (
            <p key={prompt} className="truncate font-mono text-[11px] text-foreground">
              {prompt}
            </p>
          ))
        ) : (
          <p className="font-mono text-[11px] text-faint">
            {empty ? "Lane is empty" : "Titles load as the queue drains"}
          </p>
        )}
      </div>
      {empty && !staffedName ? (
        <Button type="button" size="sm" className="mt-1 h-7 rounded-[2px] text-[11px]" disabled>
          <Plus className="size-3" />
          Assign agent
        </Button>
      ) : null}
    </div>
  )
}
