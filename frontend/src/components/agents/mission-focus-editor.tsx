import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { Project, ProjectChannelName } from "@/lib/api"
import { CHANNEL_LABELS, CHANNEL_ORDER } from "@/lib/projects"
import { cn } from "@/lib/utils"

export type MissionChannel = ProjectChannelName

type ChannelDraft = { armed: boolean; prompt: string }

const FOCUS_LABELS: Record<ProjectChannelName, string> = {
  hunter: "Hunter focus",
  research: "Research / enrichment focus",
  seo: "SEO focus",
  aeo_geo: "AEO / GEO focus",
  engage: "Engagement focus",
  publish: "Publish focus",
}

const FOCUS_HINTS: Record<ProjectChannelName, string> = {
  hunter: "Seeds hunt_queries · branch-term LLM · contact enrichment",
  research: "Seeds research_queries · summarization · grant institution lane",
  seo: "Owned SEO targets · document loop",
  aeo_geo: "AEO/GEO reviews · answer-engine documents",
  engage: "Seeds engagement_queries · social / community lane",
  publish: "Seeds publish_jobs · site publish loop",
}

function originHint(channels: MissionChannel[]): string {
  const first = channels[0]
  if (channels.length === 1 && first) {
    return `prepended to ${CHANNEL_LABELS[first].toLowerCase()} each cycle`
  }
  if (channels.length === 0) {
    return "shared origin · this agent has no channel seed"
  }
  return "prepended to hunter + research each cycle"
}

function draftsFromProject(project: Project): Record<ProjectChannelName, ChannelDraft> {
  const drafts = {} as Record<ProjectChannelName, ChannelDraft>
  for (const name of CHANNEL_ORDER) {
    drafts[name] = {
      armed: project.channels[name]?.armed ?? false,
      prompt: project.channels[name]?.prompt ?? "",
    }
  }
  return drafts
}

type MissionFocusEditorProps = {
  project: Project
  channels: MissionChannel[]
  onSave: (payload: {
    origin_prompt: string
    channels: Record<ProjectChannelName, { armed: boolean; prompt: string }>
  }) => Promise<void>
  compact?: boolean
}

export function MissionFocusEditor({
  project,
  channels,
  onSave,
  compact = false,
}: MissionFocusEditorProps) {
  const [origin, setOrigin] = useState(project.origin_prompt)
  const [drafts, setDrafts] = useState(() => draftsFromProject(project))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  useEffect(() => {
    setOrigin(project.origin_prompt)
    setDrafts(draftsFromProject(project))
  }, [project.slug, project.origin_prompt, project.channels])

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const allChannels = { ...project.channels, ...drafts } as Record<
        ProjectChannelName,
        { armed: boolean; prompt: string }
      >
      await onSave({ origin_prompt: origin, channels: allChannels })
      setSavedAt(new Date().toLocaleTimeString())
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save mission focus")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={cn("flex flex-col gap-3", compact ? "" : "rounded-[4px] border border-border p-4")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            MISSION / PRIMARY GOAL
          </p>
          <p className="text-xs text-muted-foreground">
            {project.name} · {originHint(channels)}
          </p>
        </div>
        {savedAt ? (
          <span className="font-mono text-[10px] text-working">Saved {savedAt}</span>
        ) : null}
      </div>

      <Textarea
        value={origin}
        onChange={(event) => setOrigin(event.target.value)}
        className={cn(compact ? "min-h-[88px]" : "min-h-[120px]")}
        placeholder="Institution grant-awardee buyer focus…"
      />

      {channels.map((name) => {
        const channel = drafts[name]
        if (!channel) return null
        return (
          <div key={name} className="flex flex-col gap-1.5 rounded-[4px] border border-border p-3">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-[13px] font-semibold text-foreground">{FOCUS_LABELS[name]}</p>
                <p className="font-mono text-[10px] text-muted-foreground">{FOCUS_HINTS[name]}</p>
              </div>
              <Switch
                size="sm"
                checked={channel.armed}
                onCheckedChange={(armed) =>
                  setDrafts((prev) => ({
                    ...prev,
                    [name]: { ...prev[name], armed },
                  }))
                }
              />
            </div>
            <Textarea
              value={channel.prompt}
              onChange={(event) =>
                setDrafts((prev) => ({
                  ...prev,
                  [name]: { ...prev[name], prompt: event.target.value },
                }))
              }
              className="min-h-[72px]"
            />
            <p className={cn("font-mono text-[10px]", channel.armed ? "text-working" : "text-faint")}>
              {channel.armed ? "Armed — loop will seed and read this focus." : "Off — saved but not seeded."}
            </p>
          </div>
        )
      })}

      {error ? (
        <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
          {error}
        </p>
      ) : null}

      <div className="flex justify-end">
        <Button type="button" size="sm" disabled={saving} onClick={() => void handleSave()}>
          {saving ? "Saving…" : "Save mission focus"}
        </Button>
      </div>
    </div>
  )
}
