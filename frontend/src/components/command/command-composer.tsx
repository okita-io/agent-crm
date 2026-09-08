import { useState, type KeyboardEvent } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

type CommandComposerProps = {
  disabled?: boolean
  sending?: boolean
  onSend: (message: string) => Promise<void> | void
}

export function CommandComposer({ disabled, sending, onSend }: CommandComposerProps) {
  const [draft, setDraft] = useState("")
  const empty = !draft.trim()

  async function submit() {
    if (empty || disabled || sending) return
    const message = draft
    setDraft("")
    try {
      await onSend(message)
    } catch {
      setDraft(message)
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  return (
    <form
      className="flex shrink-0 flex-col gap-2 border-t border-border bg-card px-4 py-3 lg:px-[18px]"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <Textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        disabled={disabled || sending}
        placeholder="Pause hunter, enqueue a hunt, assign a skill…"
        className="min-h-[72px] resize-none"
      />
      <div className="flex items-center justify-between gap-2">
        <p className="font-mono text-[10px] text-faint">Enter to send · Shift+Enter for a new line</p>
        <Button type="submit" size="sm" disabled={empty || disabled || sending}>
          {sending ? "Queuing…" : "Send"}
        </Button>
      </div>
    </form>
  )
}
