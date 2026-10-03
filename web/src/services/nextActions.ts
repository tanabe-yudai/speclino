/** 「次にやること」（design 5.3）。検査結果から決まった規則で並べ、LLM は使わない。 */

import type { NextAction, NextActionKind, PolicyView, RequirementView } from '@/types/project'

const RULES: { kind: NextActionKind; match: (r: RequirementView) => boolean; hint: string }[] = [
  { kind: 'violated', match: (r) => r.verdict === 'VIOLATED', hint: '反例を見る' },
  { kind: 'stale', match: (r) => r.approval === 'stale', hint: '仕様の PR で再承認する' },
  { kind: 'review', match: (r) => r.verdict === 'REVIEW' && r.approval === 'approved', hint: '人の確認が要る質問に答える' },
  { kind: 'unknown', match: (r) => r.verdict === 'UNKNOWN', hint: 'エージェントの調査の結果を見る' },
  { kind: 'unapproved', match: (r) => r.approval === 'unapproved', hint: '仕様の PR で承認する' },
]

/** 免除中の規約（期限付きの宿題）。期限の近い順。 */
function exemptedActions(policies: PolicyView[]): NextAction[] {
  return policies
    .filter((p) => p.exempted && p.waivers.length > 0)
    .map((p) => ({ policy: p, waiver: [...p.waivers].sort((a, b) => a.expires_on.localeCompare(b.expires_on))[0] }))
    .sort((a, b) => a.waiver.expires_on.localeCompare(b.waiver.expires_on))
    .map(({ policy, waiver }) => ({
      kind: 'exempted' as const,
      requirementId: policy.rule_id,
      title: policy.title,
      hint: `免除の期限 ${waiver.expires_on} までに直す: ${waiver.alternative}`,
    }))
}

/**
 * 違反 → 承認切れ → 要確認 → 免除中の規約（期限の近い順）→ 未検証 → 未承認 の順に最大 limit 件。
 * 免除中の規約は「違反でも適合でもない、期限までに直す宿題」なので要確認の後ろに置く。
 */
export function nextActions(reqs: RequirementView[], policies: PolicyView[] = [], limit = 5): NextAction[] {
  const out: NextAction[] = []
  const used = new Set<string>()
  for (const rule of RULES) {
    for (const r of reqs) {
      if (out.length >= limit) return out
      if (used.has(r.id) || !rule.match(r)) continue
      used.add(r.id)
      out.push({ kind: rule.kind, requirementId: r.id, title: r.title, hint: rule.hint })
    }
    if (rule.kind === 'review') {
      for (const a of exemptedActions(policies)) {
        if (out.length >= limit) return out
        out.push(a)
      }
    }
  }
  return out
}
