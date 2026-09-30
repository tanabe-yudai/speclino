import { VERDICT_LABELS } from '@/services/labels'
import { verdictTone } from '@/services/verdictTone'
import type { Verdict } from '@/types/finding'
import { ToneBadge } from './ToneBadge'

/** 判定の 4 値のバッジ。 */
export function VerdictBadge({ verdict, className }: { verdict: Verdict; className?: string }) {
  return (
    <ToneBadge tone={verdictTone(verdict)} className={className}>
      {VERDICT_LABELS[verdict]}
    </ToneBadge>
  )
}
