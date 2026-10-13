/** 「次にやること」。検査結果から決まった規則で並べ、LLM は使わない。 */

import { SYSTEM_RULES } from './labels'
import type { NextAction, NextActionKind, PolicyView, RequirementView } from '@/types/project'

const RULES: { kind: NextActionKind; match: (r: RequirementView) => boolean; hint: string }[] = [
  { kind: 'violated', match: (r) => r.verdict === 'VIOLATED', hint: '反例を見る' },
  { kind: 'stale', match: (r) => r.approval === 'stale', hint: '仕様の PR で再承認する' },
  { kind: 'review', match: (r) => r.verdict === 'REVIEW' && r.approval === 'approved', hint: '人の確認が要る質問に答える' },
  { kind: 'unknown', match: (r) => r.verdict === 'UNKNOWN', hint: 'エージェントの調査の結果を見る' },
  { kind: 'unapproved', match: (r) => r.approval === 'unapproved', hint: '仕様の PR で承認する' },
]

/** 規約の違反・要確認（免除中は除く）。要求と同じ段に並べる。 */
const POLICY_HINTS: Partial<Record<NextActionKind, { status: PolicyView['status']; hint: string }>> = {
  violated: { status: 'VIOLATED', hint: '検査の詳細で違反の箇所を見る' },
  review: { status: 'REVIEW', hint: '指摘を確かめて、問題なしか問題ありかを確定する' },
}

function policyActions(kind: NextActionKind, policies: PolicyView[]): NextAction[] {
  const spec = POLICY_HINTS[kind]
  if (!spec) return []
  return policies
    .filter((p) => !p.exempted && p.status === spec.status)
    .map((p) => ({ kind, requirementId: p.rule_id, title: p.title, hint: spec.hint }))
}

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
 * 規約の違反・要確認は、要求の同じ区分の後ろに並べる（上の集計と食い違わないように）。
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
      const system = SYSTEM_RULES[r.id]
      // システム規約は題が「bundle 6」のような内部の名前なので、決まった文言に置き換える
      out.push({ kind: rule.kind, requirementId: r.id, title: system?.title ?? r.title, hint: system?.hint ?? rule.hint })
    }
    for (const a of policyActions(rule.kind, policies)) {
      if (out.length >= limit) return out
      if (used.has(a.requirementId)) continue
      used.add(a.requirementId)
      out.push(a)
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
