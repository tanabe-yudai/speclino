import { useContext } from 'react'
import { MeContext, type MeState } from './meContext'

/** AppShell が 1 回だけ取った /api/me を読む（画面ごとに取り直さない）。 */
export function useMe(): MeState {
  const value = useContext(MeContext)
  if (!value) throw new Error('useMe は AppShell の中で使う')
  return value
}
