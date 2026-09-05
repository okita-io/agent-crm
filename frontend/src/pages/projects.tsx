import { useEffect, useState } from "react"

import { NewProjectCard } from "@/components/projects/new-project-card"
import { NewProjectDialog } from "@/components/projects/new-project-dialog"
import { ProjectCard } from "@/components/projects/project-card"
import { ProjectOriginRail } from "@/components/projects/project-origin-rail"
import { ProjectSettingsModal } from "@/components/projects/project-settings-modal"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { useProjects } from "@/hooks/use-projects"
import { CHANNEL_HINTS, CHANNEL_LABELS, CHANNEL_ORDER } from "@/lib/projects"
import { cn } from "@/lib/utils"

export function ProjectsPage() {
  const projects = useProjects()
  const [createOpen, setCreateOpen] = useState(false)
  const [settingsSlug, setSettingsSlug] = useState<string | null>(null)
  const [draftOrigin, setDraftOrigin] = useState("")

  const settingsProject =
    projects.projects.find((project) => project.slug === settingsSlug) ?? null

  useEffect(() => {
    setDraftOrigin(projects.selected?.origin_prompt ?? "")
  }, [projects.selected?.slug, projects.selected?.origin_prompt])

  async function flushOrigin() {
    if (!projects.selected) return
    if (draftOrigin === projects.selected.origin_prompt) return
    try {
      await projects.patch(projects.selected.slug, { origin_prompt: draftOrigin })
    } catch (err) {
      // surface via load error path
      console.error(err)
    }
  }

  const selectedForRail = projects.selected
    ? { ...projects.selected, origin_prompt: draftOrigin }
    : null

  return (
    <div className="flex min-h-0 flex-1">
      <ScrollArea className="min-w-0 flex-1">
        <div className="flex flex-col gap-3 p-4 lg:p-[18px]">
          <header className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-[22px] font-semibold text-foreground">Projects</h1>
              <p className="text-xs text-muted-foreground">
                Prompt origin · seeds every standing job queue
              </p>
            </div>
            <div className="flex items-center gap-2.5">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-working-dim px-2.5 py-1.5">
                <span className="size-[7px] rounded-full bg-working" />
                <span className="font-mono text-[10px] font-semibold text-working">
                  {projects.stats.projects} ORIGINS
                </span>
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="rounded-[4px] text-xs text-muted-foreground"
                onClick={() => setCreateOpen(true)}
              >
                New project
              </Button>
            </div>
          </header>

          {projects.error ? (
            <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
              {projects.error}
            </p>
          ) : null}

          <div className="flex gap-2">
            <StatCard label="PROJECTS" value={String(projects.stats.projects)} hint="prompt origins" />
            <StatCard
              label="LIVE SITES"
              value={String(projects.stats.live_sites)}
              hint="owned URLs for SEO"
            />
            <StatCard
              label="PRE-LAUNCH"
              value={String(projects.stats.pre_launch)}
              hint="no site / early origin"
            />
            <StatCard
              label="CHANNELS ARMED"
              value={`${projects.stats.channels_armed} / ${projects.stats.channels_total || 30}`}
              hint="per-origin job switches"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
              CHANNEL → STANDING QUEUE
            </span>
            <div className="flex flex-wrap gap-1.5">
              {CHANNEL_ORDER.map((channel) => (
                <span
                  key={channel}
                  className="rounded-[2px] bg-raised px-2 py-1 font-mono text-[10px]"
                >
                  <span className="text-primary">{CHANNEL_LABELS[channel]}</span>
                  <span className="text-faint"> · {CHANNEL_HINTS[channel]}</span>
                </span>
              ))}
            </div>
          </div>

          {projects.loading ? (
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className="h-72 rounded-[4px] bg-card" />
              ))}
            </div>
          ) : (
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {projects.projects.map((project) => (
                <ProjectCard
                  key={project.slug}
                  project={project}
                  selected={project.slug === projects.selectedSlug}
                  onSelect={() => projects.setSelectedSlug(project.slug)}
                  onEnabledChange={(enabled) => void projects.setEnabled(project.slug, enabled)}
                  onChannelChange={(channel, armed) =>
                    void projects.setChannel(project.slug, channel, armed)
                  }
                  onOpenSettings={() => setSettingsSlug(project.slug)}
                />
              ))}
              <NewProjectCard onClick={() => setCreateOpen(true)} />
            </div>
          )}

          <div className="xl:hidden">
            <div className="rounded-[4px] border border-border bg-sidebar p-3">
              <ProjectOriginRail
                project={selectedForRail}
                onChannelChange={(channel, armed) => {
                  if (!projects.selected) return
                  void projects.setChannel(projects.selected.slug, channel, armed)
                }}
                onOriginChange={setDraftOrigin}
                onOriginBlur={() => void flushOrigin()}
              />
            </div>
          </div>
        </div>
      </ScrollArea>

      <aside className="hidden h-full min-h-0 w-[332px] shrink-0 flex-col border-l border-sidebar-border bg-sidebar p-3.5 xl:flex">
        <ProjectOriginRail
          project={selectedForRail}
          onChannelChange={(channel, armed) => {
            if (!projects.selected) return
            void projects.setChannel(projects.selected.slug, channel, armed)
          }}
          onOriginChange={setDraftOrigin}
          onOriginBlur={() => void flushOrigin()}
        />
      </aside>

      <ProjectSettingsModal
        project={settingsProject}
        open={Boolean(settingsSlug)}
        onOpenChange={(open) => {
          if (!open) setSettingsSlug(null)
        }}
        onSave={async (payload) => {
          if (!settingsProject) return
          await projects.saveSettings(settingsProject.slug, payload)
        }}
        onReloadContext={async () => {
          if (!settingsProject) return
          await projects.reloadContext(settingsProject.slug)
        }}
      />

      <NewProjectDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreate={async (body) => {
          await projects.create(body)
        }}
      />
    </div>
  )
}

function StatCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1 rounded-[4px] border border-border bg-card p-3">
      <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
        {label}
      </span>
      <p className={cn("font-mono text-[22px] font-semibold text-foreground")}>{value}</p>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
    </div>
  )
}
