/** プロジェクト画面でゲートの結果を説明する（要求の件数だけでは「例外付き通過」の理由が分からないため）。 */

import type { PolicyView } from '@/types/project'
import type { GateStatus } from '@/types/run'

export interface PolicySummary {
  /** 違反した規約の数 */
  violated: number
  /** そのうち、例外で免除された finding がある規約の数 */
  waived: number
}

export function summarizePolicies(policies: PolicyView[]): PolicySummary {
  const violated = policies.filter((p) => p.status === 'VIOLATED')
  return { violated: violated.length, waived: violated.filter((p) => p.waived > 0).length }
}

/** 例外付き通過のときだけ、何を免除して通ったかを 1 文で。ほかは件数で分かるので null。 */
export function explainGate(status: GateStatus | null, summary: PolicySummary): string | null {
  if (status !== 'pass_with_exceptions') return null
  if (summary.waived === 0) return '登録済みの例外で免除した違反があるため、例外付きで通過しました'
  return `規約の違反 ${summary.waived} 件を、登録済みの例外で免除して通過しました`
}
