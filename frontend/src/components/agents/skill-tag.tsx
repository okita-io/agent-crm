import { X } from "lucide-react"

import { cn } from "@/lib/utils"

type SkillTagProps = {
  label: string
  onRemove?: () => void
}

export function SkillTag({ label, onRemove }: SkillTagProps) {
  return (
    <span className="inline-flex items-center gap-0.5 rounded-[2px] bg-raised px-1.5 py-0.5 font-mono text-[11px] text-primary">
      {label}
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${label}`}
          className={cn(
            "ml-0.5 text-faint transition-colors hover:text-blocked",
          )}
        >
          <X className="size-2.5" />
        </button>
      ) : null}
    </span>
  )
}
