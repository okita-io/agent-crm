import { padSlotIndex } from "@/lib/format"
import { cn } from "@/lib/utils"

export type SparkSlotModel = {
  title: string
  subtitle: string
  state: "IN FLIGHT" | "WAITING" | "EXTERNAL" | "FREE"
}

export function SparkSlot({ index, slot }: { index: number; slot: SparkSlotModel }) {
  const busy = slot.state === "IN FLIGHT"
  const waiting = slot.state === "WAITING"
  const external = slot.state === "EXTERNAL"
  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 flex-col gap-2 rounded-[4px] border p-3.5",
        busy
          ? "border-working bg-working-dim"
          : waiting || external
            ? "border-thinking bg-thinking-dim"
            : "border-border bg-card",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-faint">
          {padSlotIndex(index)}
        </span>
        <span
          className={cn(
            "font-mono text-[10px] font-semibold tracking-[0.6px]",
            busy ? "text-working" : waiting || external ? "text-thinking" : "text-idle",
          )}
        >
          {slot.state}
        </span>
      </div>
      <p className="text-[15px] font-semibold text-foreground">{slot.title}</p>
      <p className={cn("font-mono text-[11px]", busy ? "text-foreground" : "text-muted-foreground")}>
        {slot.subtitle}
      </p>
    </div>
  )
}
