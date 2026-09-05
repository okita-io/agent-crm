import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"

type NewProjectDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (body: {
    slug: string
    name: string
    site?: string | null
    origin_prompt?: string
    alias?: string | null
  }) => Promise<void>
}

export function NewProjectDialog({ open, onOpenChange, onCreate }: NewProjectDialogProps) {
  const [name, setName] = useState("")
  const [slug, setSlug] = useState("")
  const [site, setSite] = useState("")
  const [alias, setAlias] = useState("")
  const [origin, setOrigin] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setName("")
    setSlug("")
    setSite("")
    setAlias("")
    setOrigin("")
    setError(null)
  }

  async function handleCreate() {
    setSaving(true)
    setError(null)
    try {
      await onCreate({
        slug: slug.trim() || name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-"),
        name: name.trim(),
        site: site.trim() || null,
        alias: alias.trim() || null,
        origin_prompt: origin,
      })
      reset()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create project")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="gap-0 p-0">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>
            Writes projects/&#123;slug&#125;.yaml — loops only seed when slug matches a Brand.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3 px-[18px] py-4">
          <Field
            label="NAME"
            value={name}
            onChange={(value) => {
              setName(value)
              if (!slug) {
                setSlug(value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""))
              }
            }}
          />
          <Field label="SLUG" value={slug} onChange={setSlug} mono />
          <Field label="SITE" value={site} onChange={setSite} placeholder="https://…" mono />
          <Field label="ALIAS" value={alias} onChange={setAlias} mono />
          <div>
            <p className="mb-1 font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
              ORIGIN PROMPT
            </p>
            <Textarea value={origin} onChange={(event) => setOrigin(event.target.value)} />
          </div>
          {error ? (
            <p className="rounded-[4px] border border-blocked/40 bg-blocked-dim px-3 py-2 text-xs text-blocked">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <span />
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={saving || !name.trim()}
              onClick={() => void handleCreate()}
            >
              {saving ? "Creating…" : "Create project"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  mono,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  mono?: boolean
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="font-mono text-[9px] font-medium tracking-[0.8px] text-muted-foreground">
        {label}
      </span>
      <input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={`rounded-[2px] border border-border bg-raised px-2.5 py-2 text-[12px] text-foreground outline-none focus-visible:border-primary ${
          mono ? "font-mono" : ""
        }`}
      />
    </label>
  )
}
