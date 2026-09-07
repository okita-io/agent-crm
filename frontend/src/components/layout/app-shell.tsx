import { Outlet } from "react-router-dom"

import { Sidebar } from "@/components/layout/sidebar"
import { Topbar } from "@/components/layout/topbar"
import { TooltipProvider } from "@/components/ui/tooltip"

type AppShellProps = {
  occupied: number
  maxSlots: number
  standing: number
  unstaffed: number
}

export function AppShell({ occupied, maxSlots, standing, unstaffed }: AppShellProps) {
  return (
    <TooltipProvider>
      <div className="flex h-full overflow-hidden bg-background">
        <Sidebar
          occupied={occupied}
          maxSlots={maxSlots}
          standing={standing}
          unstaffed={unstaffed}
        />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <Topbar />
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <Outlet />
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}
