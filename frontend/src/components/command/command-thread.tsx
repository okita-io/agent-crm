import { useEffect, useRef } from "react"

import type { AgencyRequest } from "@/lib/api"
import { describeAgencyAction, unpackAgencyActions } from "@/lib/agency-actions"
import { formatAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

type CommandThreadProps = {
  requests: AgencyRequest[]
}

function statusLabel(status: AgencyRequest["status"]) {
  if (status === "pending") return "queued"
  if (status === "processing") return "interpreting"
  if (status === "failed") return "failed"
  return "done"
}

export function CommandThread({ requests }: CommandThreadProps) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [requests])

  if (!requests.length) {
    return (
      <div className="flex flex-1 items-center justify-center px-4 py-12">
        <p className="max-w-sm text-center text-sm text-muted-foreground">
          No commands yet. Ask the orchestrator to pause agents, assign a skill, or enqueue hunt,
          research, engagement, SEO, or AEO/GEO work.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 px-4 py-4 lg:px-[18px]">
      {requests.map((row) => (
        <article key={row.id} className="flex flex-col gap-2">
          <MessageBlock
            speaker="YOU"
            stamp={formatAgo(row.created_at)}
            body={row.message}
            align="end"
          />
          {row.status === "completed" ? (
            <MessageBlock
              speaker="ORCHESTRATOR"
              stamp={formatAgo(row.processed_at)}
              body={row.reply || "Done."}
              chips={actionChips(row.actions)}
            />
          ) : null}
          {row.status === "failed" ? (
            <MessageBlock
              speaker="ORCHESTRATOR"
              stamp={statusLabel(row.status)}
              body={row.error_message || "Could not handle that command."}
              tone="blocked"
            />
          ) : null}
          {row.status === "pending" || row.status === "processing" ? (
            <MessageBlock
              speaker="ORCHESTRATOR"
              stamp={statusLabel(row.status)}
              body={
                row.status === "processing"
                  ? "Interpreting with Spark…"
                  : "Queued — waiting for the orchestrator."
              }
              tone="muted"
            />
          ) : null}
        </article>
      ))}
      <div ref={endRef} />
    </div>
  )
}

function actionChips(raw: unknown): string[] {
  const { planned, results } = unpackAgencyActions(raw)
  const rows = results.length ? results : planned
  return rows.map((action) => {
    const label = describeAgencyAction(action)
    if (action.ok === false) return `${label} (skipped)`
    return label
  })
}

function MessageBlock({
  speaker,
  stamp,
  body,
  chips,
  align = "start",
  tone,
}: {
  speaker: string
  stamp: string
  body: string
  chips?: string[]
  align?: "start" | "end"
  tone?: "blocked" | "muted"
}) {
  return (
    <div className={cn("flex flex-col gap-1", align === "end" && "items-end")}>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
          {speaker}
        </span>
        <span className="font-mono text-[10px] text-faint">{stamp}</span>
      </div>
      <div
        className={cn(
          "max-w-[min(560px,100%)] rounded-[4px] border px-3 py-2 text-[13px] leading-[1.45]",
          tone === "blocked" && "border-blocked/40 bg-blocked-dim text-blocked",
          tone === "muted" && "border-border bg-raised text-muted-foreground",
          !tone && align === "end" && "border-primary/30 bg-gold-dim text-foreground",
          !tone && align === "start" && "border-border bg-card text-foreground",
        )}
      >
        <p className="whitespace-pre-wrap">{body}</p>
        {chips?.length ? (
          <div className="mt-2 flex flex-wrap gap-1">
            {chips.map((chip) => (
              <span
                key={chip}
                className="rounded-[2px] bg-raised px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
              >
                {chip}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
