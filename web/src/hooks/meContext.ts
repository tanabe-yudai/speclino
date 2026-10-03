import { createContext } from 'react'
import type { Me } from '@/types/me'

export interface MeState {
  me: Me | null
  error: string | null
  /** 取り直す。失敗は error に入れ、Promise は reject しない。 */
  reload: () => Promise<void>
}

export const MeContext = createContext<MeState | null>(null)
