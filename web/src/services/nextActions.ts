/** 「次にやること」（design 5.3）。検査結果から決まった規則で並べ、LLM は使わない。 */

import type { NextAction, NextActionKind, RequirementView } from '@/types/project'

const RULES: { kind: NextActionKind; match: (r: RequirementView) => boolean; hint: string }[] = [
  { kind: 'violated', match: (r) => r.verdict === 'VIOLATED', hint: '反例を見る' },
  { kind: 'stale', match: (r) => r.approval === 'stale', hint: '仕様の PR で再承認する' },
  { kind: 'review', match: (r) => r.verdict === 'REVIEW' && r.approval === 'approved', hint: '人の確認が要る質問に答える' },
  { kind: 'unknown', match: (r) => r.verdict === 'UNKNOWN', hint: 'エージェントの調査の結果を見る' },
  { kind: 'unapproved', match: (r) => r.approval === 'unapproved', hint: '仕様の PR で承認する' },
]

export function nextActions(reqs: RequirementView[], limit = 5): NextAction[] {
  const out: NextAction[] = []
  const used = new Set<string>()
  for (const rule of RULES) {
    for (const r of reqs) {
      if (out.length >= limit) return out
      if (used.has(r.id) || !rule.match(r)) continue
      used.add(r.id)
      out.push({ kind: rule.kind, requirementId: r.id, title: r.title, hint: rule.hint })
    }
  }
  return out
}
