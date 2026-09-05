import {
  FileSearch,
  FolderKanban,
  GitBranch,
  LayoutGrid,
  Search,
  Settings,
  Sparkles,
  Terminal,
  Users,
  type LucideIcon,
} from "lucide-react"

export type NavItem = {
  to: string
  label: string
  icon: LucideIcon
  ready: boolean
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Live Agents", icon: LayoutGrid, ready: true },
  { to: "/projects", label: "Projects", icon: FolderKanban, ready: true },
  { to: "/command", label: "Command", icon: Terminal, ready: false },
  { to: "/pipeline", label: "Pipeline", icon: GitBranch, ready: false },
  { to: "/contacts", label: "Contacts", icon: Users, ready: false },
  { to: "/hunter", label: "Hunter", icon: Search, ready: false },
  { to: "/seo", label: "SEO / GEO", icon: FileSearch, ready: false },
  { to: "/skills", label: "Skills", icon: Sparkles, ready: true },
  { to: "/settings", label: "Settings", icon: Settings, ready: false },
]
