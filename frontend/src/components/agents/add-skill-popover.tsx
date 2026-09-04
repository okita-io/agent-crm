import { Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { SkillCatalogItem } from "@/lib/api"
import { skillLabel } from "@/lib/agent-meta"

type AddSkillPopoverProps = {
  assigned: string[]
  catalog: SkillCatalogItem[]
  onAssign: (skillId: string) => void
}

export function AddSkillPopover({ assigned, catalog, onAssign }: AddSkillPopoverProps) {
  const assignedSet = new Set(assigned)
  const available = catalog.filter((skill) => !assignedSet.has(skill.id))

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="size-5 rounded-[2px] text-muted-foreground"
          aria-label="Add skill"
        >
          <Plus className="size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-1">
        {available.length ? (
          <ScrollArea className="h-56">
            <ul className="flex flex-col">
              {available.map((skill) => (
                <li key={skill.id}>
                  <button
                    type="button"
                    className="flex w-full flex-col items-start gap-0.5 rounded-[2px] px-2 py-1.5 text-left hover:bg-raised"
                    onClick={() => onAssign(skill.id)}
                  >
                    <span className="font-mono text-[11px] text-primary">
                      {skillLabel(skill.id)}
                    </span>
                    <span className="line-clamp-2 text-[10px] text-muted-foreground">
                      {skill.kind === "module" ? skill.pack : skill.summary || skill.kind}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </ScrollArea>
        ) : (
          <p className="px-2 py-3 text-[11px] text-muted-foreground">All catalog skills assigned.</p>
        )}
      </PopoverContent>
    </Popover>
  )
}
