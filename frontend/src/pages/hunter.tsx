import { Link } from "react-router-dom"

import { MissionFocusEditor } from "@/components/agents/mission-focus-editor"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { useProjects } from "@/hooks/use-projects"
import { cn } from "@/lib/utils"

const DEFAULT_SLUG = "tactic-studio"

export function HunterPage() {
  const projects = useProjects()
  const activeSlug = projects.selectedSlug ?? DEFAULT_SLUG
  const active =
    projects.projects.find((project) => project.slug === activeSlug) ??
    projects.projects.find((project) => project.slug === DEFAULT_SLUG) ??
    projects.projects[0] ??
    null

  return (
    <div className="flex min-h-0 flex-1">
      <ScrollArea className="min-w-0 flex-1">
        <div className="flex flex-col gap-3 p-4 lg:p-[18px]">
          <header className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-[22px] font-semibold text-foreground">Hunter & enrichment</h1>
              <p className="text-xs text-muted-foreground">
                Mission focus for outbound hunter and research/enrichment loops · persists to
                project prompts
              </p>
            </div>
            <Button asChild variant="outline" size="sm" className="rounded-[4px] text-xs">
              <Link to="/projects">All project prompts</Link>
            </Button>
          </header>

          {projects.error ? (
            <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
              {projects.error}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-1.5">
            {projects.loading
              ? Array.from({ length: 3 }).map((_, index) => (
                  <Skeleton key={index} className="h-7 w-24 rounded-[4px]" />
                ))
              : projects.projects.map((project) => (
                  <button
                    key={project.slug}
                    type="button"
                    onClick={() => projects.setSelectedSlug(project.slug)}
                    className={cn(
                      "rounded-[4px] px-2.5 py-1 font-mono text-[10px] transition-colors",
                      project.slug === activeSlug
                        ? "bg-primary text-primary-foreground"
                        : "bg-raised text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {project.name}
                  </button>
                ))}
          </div>

          {active ? (
            <MissionFocusEditor
              project={active}
              channels={["hunter", "research"]}
              onSave={async (payload) => {
                await projects.saveSettings(active.slug, payload)
              }}
            />
          ) : projects.loading ? (
            <Skeleton className="h-64 rounded-[4px]" />
          ) : (
            <p className="text-sm text-muted-foreground">No projects configured.</p>
          )}

          <div className="rounded-[4px] border border-border bg-raised p-3 text-xs text-muted-foreground">
            <p className="font-mono text-[9px] font-medium tracking-[0.8px] text-faint">
              OPERATOR NOTES
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              <li>
                Saved text is read on the next hunt-loop and research-loop cycle (no restart).
              </li>
              <li>
                Add supplemental seeds inline with <code className="text-foreground">query:</code>{" "}
                lines in a channel prompt.
              </li>
              <li>
                Example themes: IMLS museum grant awardees, NEH interactive exhibits, campus
                immersive federal awards.
              </li>
            </ul>
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}
