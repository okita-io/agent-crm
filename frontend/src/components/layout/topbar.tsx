import { NavLink } from "react-router-dom"

import { NAV_ITEMS } from "@/lib/nav"
import { cn } from "@/lib/utils"

export function Topbar() {
  return (
    <header className="flex items-center justify-between border-b border-sidebar-border bg-sidebar px-4 py-3 lg:hidden">
      <div className="flex items-center gap-2">
        <div className="flex size-6 items-center justify-center bg-primary font-mono text-xs font-bold text-primary-foreground">
          A
        </div>
        <span className="font-mono text-xs font-semibold tracking-[0.7px] text-foreground">
          THE AGENCY
        </span>
      </div>
      <nav className="flex items-center gap-3 overflow-x-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              cn(
                "shrink-0 text-xs",
                isActive ? "text-primary" : "text-muted-foreground",
              )
            }
          >
            {item.label.split(" ")[0]}
          </NavLink>
        ))}
      </nav>
    </header>
  )
}
