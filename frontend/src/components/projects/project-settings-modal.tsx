import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { Project, ProjectChannelName } from "@/lib/api"
import { CHANNEL_HINTS, CHANNEL_LABELS, CHANNEL_ORDER, statusTone } from "@/lib/projects"
import { cn } from "@/lib/utils"

type ProjectSettingsModalProps = {
  project: Project | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (payload: {
    origin_prompt: string
    channels: Record<ProjectChannelName, { armed: boolean; prompt: string }>
  }) => Promise<void>
  onReloadContext: () => Promise<void>
}

export function ProjectSettingsModal({
  project,
  open,
  onOpenChange,
  onSave,
  onReloadContext,
}: ProjectSettingsModalProps) {
  const [origin, setOrigin] = useState("")
  const [channels, setChannels] = useState<
    Record<ProjectChannelName, { armed: boolean; prompt: string }>
  >({} as Record<ProjectChannelName, { armed: boolean; prompt: string }>)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!project || !open) return
    setOrigin(project.origin_prompt)
    const next = {} as Record<ProjectChannelName, { armed: boolean; prompt: string }>
    for (const name of CHANNEL_ORDER) {
      next[name] = {
        armed: project.channels[name]?.armed ?? false,
        prompt: project.channels[name]?.prompt ?? "",
      }
    }
    setChannels(next)
    setError(null)
  }, [project, open])

  if (!project) return null
  const tone = statusTone(project.status)
  const armedCount = CHANNEL_ORDER.filter((name) => channels[name]?.armed).length

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      await onSave({ origin_prompt: origin, channels })
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save prompts")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-0 p-0" showCloseButton>
        <DialogHeader>
          <DialogTitle>{project.name} prompts</DialogTitle>
          <DialogDescription>
            {project.slug} · {tone.label.toLowerCase()} · {armedCount} of 6 channels armed
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[min(70vh,720px)] flex-col gap-3 overflow-y-auto px-[18px] py-4">
          <div className="rounded-[4px] border border-primary bg-raised p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
                PRIMARY GOAL
              </p>
              <p className="font-mono text-[10px] text-primary">prepended to every armed task</p>
            </div>
            <Textarea
              value={origin}
              onChange={(event) => setOrigin(event.target.value)}
              className="min-h-[110px]"
            />
            <p className="mt-2 font-mono text-[10px] text-faint">
              {project.context_file || "no context file"} · origin for{" "}
              {project.seeded_loops.join(" · ") || "no loops"}
            </p>
          </div>

          <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            TASK PROMPTS
          </p>

          {CHANNEL_ORDER.map((name) => {
            const channel = channels[name]
            if (!channel) return null
            return (
              <div key={name} className="flex flex-col gap-1.5 rounded-[4px] border border-border p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[13px] font-semibold text-foreground">
                      {CHANNEL_LABELS[name]}
                    </p>
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {CHANNEL_HINTS[name]} · after primary goal
                    </p>
                  </div>
                  <Switch
                    size="sm"
                    checked={channel.armed}
                    onCheckedChange={(armed) =>
                      setChannels((prev) => ({
                        ...prev,
                        [name]: { ...prev[name], armed },
                      }))
                    }
                  />
                </div>
                <Textarea
                  value={channel.prompt}
                  onChange={(event) =>
                    setChannels((prev) => ({
                      ...prev,
                      [name]: { ...prev[name], prompt: event.target.value },
                    }))
                  }
                  className={cn("min-h-[72px]", !channel.armed && "text-muted-foreground")}
                />
                <p
                  className={cn(
                    "font-mono text-[10px]",
                    channel.armed ? "text-working" : "text-faint",
                  )}
                >
                  {channel.armed
                    ? `Armed — will seed for this origin.`
                    : `Off — saved, but will not seed until armed.`}
                </p>
              </div>
            )
          })}

          {error ? (
            <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => void onReloadContext()}
          >
            Reload {project.context_file || "brand-context"}
          </Button>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="button" size="sm" disabled={saving} onClick={() => void handleSave()}>
              {saving ? "Saving…" : "Save prompts"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
