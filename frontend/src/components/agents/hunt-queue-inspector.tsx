import { useEffect, useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  api,
  type HuntQuery,
  type HuntQueryList,
  type HuntQueryStatus,
} from "@/lib/api"
import { cn } from "@/lib/utils"

const PAGE_SIZE = 50
const BRANDS = [
  "tactic-studio",
  "midnightsatin",
  "celestial-nexus",
  "heybuddy",
  "best-biryani",
] as const
const ORIGIN_CHIPS = ["branch", "marketing", "seed_pack", "community"] as const
const STATUS_TABS: { id: HuntQueryStatus | "all"; label: string }[] = [
  { id: "pending", label: "Pending" },
  { id: "running", label: "Running" },
  { id: "pending_review", label: "Review" },
  { id: "failed", label: "Failed" },
  { id: "completed", label: "Done" },
  { id: "rejected", label: "Tossed" },
  { id: "all", label: "All" },
]

type HuntQueueInspectorProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onChanged?: () => void
}

function statusTone(status: HuntQueryStatus) {
  if (status === "running") return "bg-working-dim text-working"
  if (status === "pending") return "bg-gold-dim text-primary"
  if (status === "pending_review") return "bg-thinking-dim text-thinking"
  if (status === "failed") return "bg-blocked-dim text-blocked"
  if (status === "rejected") return "bg-blocked-dim text-blocked"
  return "bg-raised text-muted-foreground"
}

function canToss(status: HuntQueryStatus) {
  return status === "pending" || status === "pending_review" || status === "failed"
}

export function HuntQueueInspector({ open, onOpenChange, onChanged }: HuntQueueInspectorProps) {
  const [status, setStatus] = useState<HuntQueryStatus | "all">("pending")
  const [brand, setBrand] = useState("")
  const [originPrefix, setOriginPrefix] = useState("")
  const [search, setSearch] = useState("")
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<number[]>([])
  const [data, setData] = useState<HuntQueryList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const drainOrder = status === "pending" || status === "pending_review" || status === "running"

  async function load() {
    try {
      const result = await api.huntQueries({
        brand: brand || undefined,
        status: status === "all" ? undefined : status,
        origin_prefix: originPrefix || undefined,
        q: search.trim() || undefined,
        drain_order: drainOrder,
        limit: PAGE_SIZE,
        offset,
      })
      setData(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load hunter queue")
    }
  }

  useEffect(() => {
    if (!open) return
    void load()
    const id = window.setInterval(() => {
      void load()
    }, 8000)
    return () => window.clearInterval(id)
  }, [open, status, brand, originPrefix, search, offset])

  useEffect(() => {
    setOffset(0)
    setSelected([])
  }, [status, brand, originPrefix, search])

  const counts = data?.by_status ?? {}
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const selectedSet = useMemo(() => new Set(selected), [selected])
  const pageIds = items.filter((row) => canToss(row.status)).map((row) => row.id)
  const matchingNarrowed = Boolean(brand || originPrefix || search.trim())
  const tossableStatus =
    status === "pending" || status === "pending_review" || status === "failed" ? status : null

  async function runAction(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      setSelected([])
      await load()
      onChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update hunter queue")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(960px,calc(100vw-2rem))] gap-0 p-0" showCloseButton>
        <DialogHeader>
          <DialogTitle>Hunter queue</DialogTitle>
          <DialogDescription>
            {total.toLocaleString()} matching · pending {(counts.pending ?? 0).toLocaleString()} ·
            running {counts.running ?? 0} · failed {counts.failed ?? 0} · tossed{" "}
            {(counts.rejected ?? 0).toLocaleString()}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 px-[18px] py-3">
          <div className="flex flex-wrap gap-1">
            {STATUS_TABS.map((tab) => (
              <Button
                key={tab.id}
                type="button"
                size="xs"
                variant={status === tab.id ? "default" : "outline"}
                className="rounded-[2px]"
                onClick={() => setStatus(tab.id)}
              >
                {tab.label}
                {tab.id !== "all" ? (
                  <span className="font-mono text-[10px] opacity-70">
                    {(counts[tab.id] ?? 0).toLocaleString()}
                  </span>
                ) : null}
              </Button>
            ))}
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
                BRAND
              </span>
              <select
                value={brand}
                onChange={(event) => setBrand(event.target.value)}
                className="rounded-[2px] border border-border bg-raised px-2.5 py-2 font-mono text-[12px] text-foreground outline-none focus-visible:border-primary"
              >
                <option value="">all brands</option>
                {BRANDS.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
                ORIGIN PREFIX
              </span>
              <input
                value={originPrefix}
                onChange={(event) => setOriginPrefix(event.target.value)}
                placeholder="branch"
                className="rounded-[2px] border border-border bg-raised px-2.5 py-2 font-mono text-[12px] text-foreground outline-none focus-visible:border-primary"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
                SEARCH
              </span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="query or origin"
                className="rounded-[2px] border border-border bg-raised px-2.5 py-2 text-[12px] text-foreground outline-none focus-visible:border-primary"
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-1">
            {ORIGIN_CHIPS.map((chip) => (
              <Button
                key={chip}
                type="button"
                size="xs"
                variant={originPrefix === chip ? "default" : "outline"}
                className="rounded-[2px] font-mono"
                onClick={() => setOriginPrefix(originPrefix === chip ? "" : chip)}
              >
                {chip}
              </Button>
            ))}
          </div>
          {error ? (
            <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
              {error}
            </p>
          ) : null}
        </div>

        <ScrollArea className="max-h-[48vh] border-t border-border">
          <table className="w-full text-left">
            <thead className="sticky top-0 bg-card">
              <tr className="font-mono text-[9px] tracking-[0.8px] text-muted-foreground">
                <th className="w-8 px-3 py-2">
                  <input
                    type="checkbox"
                    checked={pageIds.length > 0 && pageIds.every((id) => selectedSet.has(id))}
                    onChange={(event) => {
                      if (event.target.checked) {
                        setSelected(Array.from(new Set([...selected, ...pageIds])))
                      } else {
                        setSelected(selected.filter((id) => !pageIds.includes(id)))
                      }
                    }}
                  />
                </th>
                <th className="px-2 py-2">QUERY</th>
                <th className="px-2 py-2">ORIGIN</th>
                <th className="px-2 py-2">BRAND</th>
                <th className="px-2 py-2">P</th>
                <th className="px-2 py-2">STATUS</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {items.length ? (
                items.map((row) => (
                  <QueryRow
                    key={row.id}
                    row={row}
                    checked={selectedSet.has(row.id)}
                    busy={busy}
                    onToggle={(checked) => {
                      setSelected((prev) =>
                        checked ? [...prev, row.id] : prev.filter((id) => id !== row.id),
                      )
                    }}
                    onToss={() =>
                      void runAction(() => api.rejectHuntQuery(row.id, "operator toss"))
                    }
                    onKeep={() => void runAction(() => api.keepHuntQuery(row.id))}
                    onRetry={() => void runAction(() => api.retryHuntQuery(row.id))}
                  />
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                    No hunt queries match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </ScrollArea>

        <DialogFooter className="flex-wrap gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Prev
            </Button>
            <span className="font-mono text-[11px] text-muted-foreground">
              {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of{" "}
              {total.toLocaleString()}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={busy || selected.length === 0}
              onClick={() =>
                void runAction(() => api.rejectHuntQueries(selected, "operator toss"))
              }
            >
              Toss selected ({selected.length})
            </Button>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={busy || !tossableStatus || !matchingNarrowed}
              onClick={() => {
                const label = [
                  tossableStatus,
                  brand || null,
                  originPrefix || null,
                  search.trim() || null,
                ]
                  .filter(Boolean)
                  .join(" · ")
                if (!window.confirm(`Toss all matching ${label}?`)) return
                void runAction(() =>
                  api.rejectMatchingHuntQueries({
                    status: tossableStatus!,
                    brand: brand || undefined,
                    origin_prefix: originPrefix || undefined,
                    q: search.trim() || undefined,
                    reason: "operator toss",
                  }),
                )
              }}
            >
              Toss matching filter
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function QueryRow({
  row,
  checked,
  busy,
  onToggle,
  onToss,
  onKeep,
  onRetry,
}: {
  row: HuntQuery
  checked: boolean
  busy: boolean
  onToggle: (checked: boolean) => void
  onToss: () => void
  onKeep: () => void
  onRetry: () => void
}) {
  return (
    <tr className="border-t border-border align-top">
      <td className="px-3 py-2">
        <input
          type="checkbox"
          disabled={!canToss(row.status)}
          checked={checked}
          onChange={(event) => onToggle(event.target.checked)}
        />
      </td>
      <td className="max-w-[320px] px-2 py-2 font-mono text-[11px] leading-[1.35] text-foreground">
        {row.query}
      </td>
      <td className="max-w-[180px] truncate px-2 py-2 font-mono text-[10px] text-muted-foreground">
        {row.origin}
      </td>
      <td className="px-2 py-2 font-mono text-[10px] text-muted-foreground">{row.brand}</td>
      <td className="px-2 py-2 font-mono text-[10px] text-muted-foreground">{row.priority}</td>
      <td className="px-2 py-2">
        <span className={cn("rounded-[2px] px-1.5 py-0.5 font-mono text-[10px]", statusTone(row.status))}>
          {row.status}
        </span>
      </td>
      <td className="px-2 py-2">
        <div className="flex flex-wrap justify-end gap-1">
          {row.status === "pending_review" ? (
            <Button type="button" size="xs" variant="outline" disabled={busy} onClick={onKeep}>
              Keep
            </Button>
          ) : null}
          {row.status === "failed" ? (
            <Button type="button" size="xs" variant="outline" disabled={busy} onClick={onRetry}>
              Retry
            </Button>
          ) : null}
          {canToss(row.status) ? (
            <Button type="button" size="xs" variant="destructive" disabled={busy} onClick={onToss}>
              Toss
            </Button>
          ) : null}
        </div>
      </td>
    </tr>
  )
}
