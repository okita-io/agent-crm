import { Plus } from "lucide-react"

type NewProjectCardProps = {
  onClick: () => void
}

export function NewProjectCard({ onClick }: NewProjectCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex min-h-[200px] flex-1 flex-col items-center justify-center gap-2.5 rounded-[4px] border border-dashed border-border bg-card p-3.5 text-center"
    >
      <span className="flex size-9 items-center justify-center rounded-[4px] bg-raised">
        <Plus className="size-4 text-primary" />
      </span>
      <span className="text-sm font-semibold text-foreground">New project</span>
      <span className="text-[11px] text-muted-foreground">
        Name, site, origin prompt, job switches
      </span>
    </button>
  )
}
