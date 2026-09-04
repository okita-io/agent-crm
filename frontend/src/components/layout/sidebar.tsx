import { NavLink } from "react-router-dom"

import { NAV_ITEMS } from "@/lib/nav"
import { cn } from "@/lib/utils"

type SidebarProps = {
  occupied: number
  maxSlots: number
  standing: number
  unstaffed: number
}

export function Sidebar({ occupied, maxSlots, standing, unstaffed }: SidebarProps) {
  const bars = Array.from({ length: maxSlots }, (_, index) => index < occupied)

  return (
    <aside className="hidden h-svh w-[212px] shrink-0 flex-col justify-between border-r border-sidebar-border bg-sidebar lg:flex">
      <div className="flex flex-col gap-6 pt-5">
        <div className="flex items-center gap-2.5 px-4">
          <div className="flex size-7 items-center justify-center bg-primary font-mono text-sm font-bold text-primary-foreground">
            A
          </div>
          <div className="flex flex-col gap-px">
            <span className="font-mono text-[13px] font-semibold tracking-[0.8px] text-foreground">
              THE AGENCY
            </span>
            <span className="text-[10px] text-muted-foreground">CRM · SEO · Marketing</span>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 px-4 py-2.5 text-xs font-medium",
                  isActive
                    ? "border-l-2 border-primary bg-gold-dim text-primary"
                    : "border-l-2 border-transparent text-muted-foreground hover:bg-raised hover:text-foreground",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={cn("size-[15px]", isActive ? "text-primary" : "text-muted-foreground")} />
                  {item.label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>
      <div className="flex flex-col gap-2 px-4 py-3">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
            SPARK
          </span>
          <span className="font-mono text-[11px] text-primary">
            {occupied} / {maxSlots}
          </span>
        </div>
        <div className="flex gap-1">
          {bars.map((on, index) => (
            <div
              key={index}
              className={cn("h-1 flex-1 rounded-px", on ? "bg-primary" : "bg-raised")}
            />
          ))}
        </div>
        <p className="font-mono text-[10px] text-faint">
          {standing} standing · {unstaffed} unstaffed
        </p>
      </div>
    </aside>
  )
}
