/** プロジェクト画面でゲートの結果を説明する（要求の件数だけでは「例外付き通過」の理由が分からないため）。 */

import type { PolicyView } from '@/types/project'
import type { GateStatus } from '@/types/run'

export interface PolicySummary {
  /** 違反した規約（免除されていないものが残る）の数 */
  violated: number
  /** 免除中（違反がすべて期限付きの例外で免除されている）の規約の数 */
  exempted: number
  /** 免除の最短の期限（YYYY-MM-DD）。免除が無ければ null */
  earliestExpiry: string | null
}

export function summarizePolicies(policies: PolicyView[]): PolicySummary {
  const exempted = policies.filter((p) => p.exempted)
  const expiries = exempted.flatMap((p) => p.waivers.map((w) => w.expires_on)).sort()
  return {
    violated: policies.filter((p) => p.status === 'VIOLATED' && !p.exempted).length,
    exempted: exempted.length,
    earliestExpiry: expiries[0] ?? null,
  }
}

/** 例外付き通過のときだけ、何を免除して通ったかを 1 文で。ほかは件数で分かるので null。 */
export function explainGate(status: GateStatus | null, summary: PolicySummary): string | null {
  if (status !== 'pass_with_exceptions') return null
  if (summary.exempted === 0) return '登録済みの例外で免除した違反があるため、例外付きで通過しました'
  const due = summary.earliestExpiry ? `（最短の期限 ${summary.earliestExpiry} までに直す）` : ''
  return `規約 ${summary.exempted} 件の違反を、期限付きの例外で免除中です${due}`
}
