import { createContext, useContext, type ReactNode } from "react"

import { useFloor, type FloorState } from "@/hooks/use-floor"

type FloorContextValue = FloorState & {
  reload: () => Promise<void>
  setEnabled: (name: string, enabled: boolean) => Promise<void>
  assignSkill: (name: string, skillId: string) => Promise<void>
  unassignSkill: (name: string, skillId: string) => Promise<void>
  unassignSkillEverywhere: (skillId: string) => Promise<void>
}

const FloorContext = createContext<FloorContextValue | null>(null)

export function FloorProvider({ children }: { children: ReactNode }) {
  const floor = useFloor()
  return <FloorContext.Provider value={floor}>{children}</FloorContext.Provider>
}

export function useFloorContext(): FloorContextValue {
  const value = useContext(FloorContext)
  if (!value) {
    throw new Error("useFloorContext must be used within FloorProvider")
  }
  return value
}
