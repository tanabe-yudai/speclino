import type { VerdictTone } from '@/types/components'
import type { VerdictCounts } from '@/types/project'
import type { GateStatus } from '@/types/run'

/** 適合の割合（0〜1）。未解析も分母に入れる。要求が 1 件も無ければ null（0% と言わない）。 */
export function healthRatio(counts: VerdictCounts | null, unanalyzed = 0): number | null {
  if (!counts) return null
  const total = counts.VERIFIED_IN_SCOPE + counts.VIOLATED + counts.UNKNOWN + counts.REVIEW + unanalyzed
  return total === 0 ? null : counts.VERIFIED_IN_SCOPE / total
}

/** 表示用の百分率。切り捨てる（違反が 1 件でもあれば 100 と出さない）。 */
export function healthPercent(ratio: number): number {
  return Math.floor(ratio * 100)
}

/** リングの色。要求の適合率だけでは全体が健全に見えてしまうので、ゲートの結果に合わせる。 */
export function healthTone(gate: GateStatus | null): Exclude<VerdictTone, 'unk'> {
  if (gate === 'fail') return 'bad'
  if (gate === 'pass_with_exceptions') return 'rev'
  return 'ok'
}
