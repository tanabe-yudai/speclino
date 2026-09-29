import { createContext } from 'react'
import type { Me } from '@/types/me'

export interface MeState {
  me: Me | null
  error: string | null
  reload: () => void
}

export const MeContext = createContext<MeState | null>(null)
