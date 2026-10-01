import { describe, expect, it } from 'vitest'
import { baseForDraft, diffPackages, newRule, parsePaths, ruleProblems, upsertRule } from './policyDraft'
import type { PoliciesView, PolicyPackage } from '@/types/policy'

const pkg = (rules = [newRule('DOC-001')]): PolicyPackage => ({ id: 'team', version: '1', status: 'active', owner: 'o', rules })
const view = (over: Partial<PoliciesView>): PoliciesView => ({
  active: null, draft: null, draft_hash: null, can_approve: false, verifiers: {}, results: {}, ...over,
})
const rev = (p: PolicyPackage, status: 'draft' | 'active') => ({ team: 't', revision: 1, status, package: p, author: 'a', approver: null, note: '' })

describe('policyDraft', () => {
  it('下書きがあれば下書き、無ければ有効な改訂、どちらも無ければ空から始める', () => {
    const d = pkg([newRule('D')])
    const a = pkg([newRule('A')])
    expect(baseForDraft(view({ draft: rev(d, 'draft'), active: rev(a, 'active') })).rules[0].id).toBe('D')
    expect(baseForDraft(view({ active: rev(a, 'active') })).rules[0].id).toBe('A')
    expect(baseForDraft(view({})).rules).toEqual([])
  })

  it('ルールの追加と置き換え（ID を変えた編集は元の位置で置き換える）', () => {
    const p = pkg([newRule('A'), newRule('B')])
    expect(upsertRule(p, newRule('C')).rules.map((r) => r.id)).toEqual(['A', 'B', 'C'])
    expect(upsertRule(p, { ...newRule('B2') }, 'B').rules.map((r) => r.id)).toEqual(['A', 'B2'])
    expect(upsertRule(p, { ...newRule('A'), level: 'mandatory' }).rules[0].level).toBe('mandatory')
  })

  it('差分: 追加・削除・変わった項目', () => {
    const before = pkg([newRule('A'), newRule('B')])
    const after = pkg([{ ...newRule('A'), level: 'mandatory', title: '新しい題' }, newRule('C')])
    expect(diffPackages(before, after)).toEqual([
      { ruleId: 'A', kind: 'changed', fields: ['level', 'title'] },
      { ruleId: 'C', kind: 'added', fields: [] },
      { ruleId: 'B', kind: 'removed', fields: [] },
    ])
    expect(diffPackages(null, after).map((c) => c.kind)).toEqual(['added', 'added'])
  })

  it('適用範囲は改行かカンマで区切り、空は捨てる', () => {
    expect(parsePaths('specs/*.md, docs/**\n\n  src/**  ')).toEqual(['specs/*.md', 'docs/**', 'src/**'])
  })

  it('新しいルールは助言・検証器なし・例外可', () => {
    expect(newRule('X')).toMatchObject({ id: 'X', level: 'advisory', verifier: null, exceptions_allowed: true, on_missing_input: 'unknown' })
  })

  it('保存できない理由（空の ID・コロン・重複・空の題・検査器の無い必須）', () => {
    const ok = { ...newRule('A'), title: '題' }
    expect(ruleProblems(ok, ['B'], undefined)).toEqual([])
    expect(ruleProblems({ ...ok, id: ' ' }, [], undefined)).toContain('ID を入れてください')
    expect(ruleProblems({ ...ok, id: 'A:B' }, [], undefined)).toContain('ID にコロン（:）は使えません')
    expect(ruleProblems(ok, ['A'], undefined)).toContain('同じ ID のルールがあります')
    expect(ruleProblems(ok, ['A'], 'A')).toEqual([])
    expect(ruleProblems({ ...ok, title: '' }, [], undefined)).toContain('題を入れてください')
    expect(ruleProblems({ ...ok, level: 'mandatory' }, [], undefined)).toContain('必須のルールには検査器が要ります')
  })
})
