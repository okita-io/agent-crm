import { useCallback, useEffect, useState } from "react"

import { api, type AgencyRequest } from "@/lib/api"

const IDLE_MS = 5000
const ACTIVE_MS = 2000

export function useCommands() {
  const [requests, setRequests] = useState<AgencyRequest[]>([])
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

  const pending = requests.some(
    (row) => row.status === "pending" || row.status === "processing",
  )

  const load = useCallback(async () => {
    try {
      const rows = await api.agencyRequests()
      setRequests(rows)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load commands")
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const id = window.setInterval(() => {
      void load()
    }, pending ? ACTIVE_MS : IDLE_MS)
    return () => window.clearInterval(id)
  }, [load, pending])

  const send = useCallback(async (message: string) => {
    const cleaned = message.trim()
    if (!cleaned) return
    setSending(true)
    setError(null)
    try {
      const row = await api.submitAgencyRequest(cleaned)
      setRequests((prev) => {
        const without = prev.filter((item) => item.id !== row.id)
        return [...without, row]
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue command")
      throw err
    } finally {
      setSending(false)
      void load()
    }
  }, [load])

  return { requests, error, sending, pending, send, reload: load }
}
