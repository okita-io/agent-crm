import { cn } from "@/lib/utils"

type LeadChartProps = {
  label: string
  value: string
  delta: string
  deltaPositive: boolean
  bars: number[]
}

export function LeadChart({ label, value, delta, deltaPositive, bars }: LeadChartProps) {
  const peak = Math.max(...bars, 1)
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2 rounded-[4px] border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          {label}
        </span>
        <span
          className={cn(
            "font-mono text-[10px]",
            deltaPositive ? "text-working" : "text-muted-foreground",
          )}
        >
          {delta}
        </span>
      </div>
      <p className="font-mono text-[22px] font-semibold text-foreground">{value}</p>
      <div className="flex h-[52px] items-end gap-0.5">
        {bars.map((bar, index) => (
          <div
            key={index}
            className={cn("min-h-px flex-1 rounded-px", bar > 0 ? "bg-primary" : "bg-raised")}
            style={{ height: `${Math.max(6, (bar / peak) * 52)}px` }}
          />
        ))}
      </div>
    </div>
  )
}
