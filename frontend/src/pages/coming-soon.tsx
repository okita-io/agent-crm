import { useLocation } from "react-router-dom"

import { NAV_ITEMS } from "@/lib/nav"

export function ComingSoonPage() {
  const location = useLocation()
  const item = NAV_ITEMS.find((entry) => entry.to === location.pathname)
  const label = item?.label ?? "This view"

  return (
    <div className="flex flex-1 flex-col gap-2 p-6">
      <h1 className="text-[22px] font-semibold text-foreground">{label}</h1>
      <p className="max-w-lg text-sm text-muted-foreground">
        Live Agents is the first Vite surface. {label} still runs in the Streamlit dashboard on
        port 8501 until this shell grows the rest of the tabs.
      </p>
    </div>
  )
}
