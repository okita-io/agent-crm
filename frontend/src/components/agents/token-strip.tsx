import { formatTokenCount, formatTokenRate, formatUsd } from "@/lib/format"

type TokenStripProps = {
  prompt: number
  completion: number
  hourly: number
  saved: number
}

export function TokenStrip({ prompt, completion, hourly, saved }: TokenStripProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-[4px] border border-border bg-card px-3 py-2">
      <Metric label="IN" value={formatTokenCount(prompt)} />
      <Metric label="OUT" value={formatTokenCount(completion)} />
      <Metric label="AVG / HR" value={hourly > 0 ? formatTokenRate(hourly) : "—"} />
      <Metric label="CLOUD AVOIDED" value={formatUsd(saved)} accent />
    </div>
  )
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
        {label}
      </span>
      <span className={accent ? "font-mono text-sm font-semibold text-primary" : "font-mono text-sm font-semibold text-foreground"}>
        {value}
      </span>
    </div>
  )
}
