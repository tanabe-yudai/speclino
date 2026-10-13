/** 判定の 4 値と色の対応（意味と色の対応は固定）。 */

import type { VerdictTone } from '@/types/components'
import type { Verdict } from '@/types/finding'

const TONE: Record<Verdict, VerdictTone> = {
  VERIFIED_IN_SCOPE: 'ok',
  VIOLATED: 'bad',
  UNKNOWN: 'unk',
  REVIEW: 'rev',
}

export function verdictTone(verdict: Verdict): VerdictTone {
  return TONE[verdict]
}
