import { BrowserRouter, Route, Routes } from "react-router-dom"

import { AppShell } from "@/components/layout/app-shell"
import { FloorProvider, useFloorContext } from "@/hooks/floor-context"
import { isPlaceholder } from "@/lib/roster"
import { ComingSoonPage } from "@/pages/coming-soon"
import { LiveAgentsPage } from "@/pages/live-agents"
import { ProjectsPage } from "@/pages/projects"
import { SkillsPage } from "@/pages/skills"

function ShellLayout() {
  const floor = useFloorContext()
  const occupied =
    (floor.spark?.local_in_flight ?? 0) + (floor.spark?.external_upstream_slots ?? 0)
  const maxSlots = floor.spark?.max_concurrency ?? 4
  const standing = floor.agents.filter((agent) => !isPlaceholder(agent.name, agent.placeholder)).length
  const unstaffed = floor.agents.filter((agent) => isPlaceholder(agent.name, agent.placeholder)).length

  return (
    <AppShell
      occupied={Math.min(occupied, maxSlots)}
      maxSlots={maxSlots}
      standing={standing}
      unstaffed={unstaffed}
    />
  )
}

export default function App() {
  return (
    <FloorProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<ShellLayout />}>
            <Route path="/" element={<LiveAgentsPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/command" element={<ComingSoonPage />} />
            <Route path="/pipeline" element={<ComingSoonPage />} />
            <Route path="/contacts" element={<ComingSoonPage />} />
            <Route path="/hunter" element={<ComingSoonPage />} />
            <Route path="/seo" element={<ComingSoonPage />} />
            <Route path="/skills" element={<SkillsPage />} />
            <Route path="/settings" element={<ComingSoonPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </FloorProvider>
  )
}
