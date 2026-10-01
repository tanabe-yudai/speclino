/** チーム規約の下書きの編集（S43、design 6 章）。画面の部品から使う純粋関数。 */

import type { PoliciesView, PolicyPackage, PolicyRule, RuleChange } from '@/types/policy'

const EMPTY: PolicyPackage = { id: 'team', version: '1', status: 'active', owner: 'team', rules: [] }

/** 編集の起点: 下書き → 有効な改訂 → 空。 */
export function baseForDraft(view: PoliciesView): PolicyPackage {
  return view.draft?.package ?? view.active?.package ?? EMPTY
}

/** ルールを追加する。originalId（編集前の ID）か同じ ID があればその位置で置き換える。 */
export function upsertRule(pkg: PolicyPackage, rule: PolicyRule, originalId?: string): PolicyPackage {
  const key = originalId ?? rule.id
  const i = pkg.rules.findIndex((r) => r.id === key)
  const rules = i < 0 ? [...pkg.rules, rule] : pkg.rules.map((r, j) => (j === i ? rule : r))
  return { ...pkg, rules }
}

const FIELDS: (keyof PolicyRule)[] = ['level', 'title', 'text', 'applies_to', 'verifier', 'on_missing_input', 'exceptions_allowed', 'version']

/** before（有効な改訂）から after（下書き）への変化。after の順、削除は最後。 */
export function diffPackages(before: PolicyPackage | null, after: PolicyPackage): RuleChange[] {
  const old = new Map((before?.rules ?? []).map((r) => [r.id, r]))
  const changes: RuleChange[] = []
  for (const r of after.rules) {
    const o = old.get(r.id)
    if (!o) {
      changes.push({ ruleId: r.id, kind: 'added', fields: [] })
      continue
    }
    const fields = FIELDS.filter((f) => JSON.stringify(o[f] ?? null) !== JSON.stringify(r[f] ?? null))
    if (fields.length > 0) changes.push({ ruleId: r.id, kind: 'changed', fields: fields as string[] })
  }
  const kept = new Set(after.rules.map((r) => r.id))
  for (const r of before?.rules ?? []) {
    if (!kept.has(r.id)) changes.push({ ruleId: r.id, kind: 'removed', fields: [] })
  }
  return changes
}

export function parsePaths(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

export function newRule(id: string): PolicyRule {
  return {
    id,
    version: '1.0.0',
    level: 'advisory',
    title: '',
    text: '',
    applies_to: { paths: [] },
    verifier: null,
    on_missing_input: 'unknown',
    exceptions_allowed: true,
  }
}
