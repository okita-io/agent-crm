import { Cpu, Database, Flame, Globe, Search } from "lucide-react"

import type { ResourceKind } from "@/lib/agent-meta"

const ICONS = {
  globe: Globe,
  flame: Flame,
  cpu: Cpu,
  database: Database,
  search: Search,
} as const

export function ResourceChip({ icon, label }: { icon: ResourceKind; label: string }) {
  const Icon = ICONS[icon]
  return (
    <span className="inline-flex items-center gap-1 rounded-[2px] border border-border bg-surface px-1.5 py-0.5">
      <Icon className="size-[11px] text-muted-foreground" />
      <span className="font-mono text-[10px] text-foreground">{label}</span>
    </span>
  )
}
