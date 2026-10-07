import { describe, expect, it } from 'vitest'
import { nextActions } from './nextActions'
import type { RequirementView } from '@/types/project'

function req(id: string, over: Partial<RequirementView>): RequirementView {
  return {
    id, behavior_id: 'b', title: null, spec_path: null, line_start: null, line_end: null,
    approval: 'approved', tests_declared: 0, verdict: 'VERIFIED_IN_SCOPE', finding_ids: [], removed: false, ...over,
  }
}

describe('nextActions', () => {
  it('違反 → 承認切れ → 要確認 → 未検証 → 未承認 の順', () => {
    const reqs = [
      req('U', { approval: 'unapproved', verdict: 'REVIEW' }),
      req('K', { verdict: 'UNKNOWN' }),
      req('Q', { verdict: 'REVIEW' }),
      req('S', { approval: 'stale', verdict: 'REVIEW' }),
      req('V', { verdict: 'VIOLATED' }),
      req('OK', {}),
    ]
    expect(nextActions(reqs).map((a) => [a.kind, a.requirementId])).toEqual([
      ['violated', 'V'], ['stale', 'S'], ['review', 'Q'], ['unknown', 'K'], ['unapproved', 'U'],
    ])
  })

  it('未承認の要確認は「要確認」ではなく「未承認」に入る', () => {
    expect(nextActions([req('U', { approval: 'unapproved', verdict: 'REVIEW' })]).map((a) => a.kind)).toEqual(['unapproved'])
  })

  it('最大 5 件、同じ要求は 1 回だけ', () => {
    const reqs = Array.from({ length: 8 }, (_, i) => req(`V${i}`, { verdict: 'VIOLATED' }))
    const out = nextActions(reqs)
    expect(out).toHaveLength(5)
    expect(new Set(out.map((a) => a.requirementId)).size).toBe(5)
  })

  it('何も無ければ空', () => {
    expect(nextActions([req('OK', {})])).toEqual([])
  })

  it('免除中の規約は、要確認の後ろに期限の近い順で入る', () => {
    const policies = [
      { rule_id: 'P-LATE', origin: 'team' as const, title: '遅い', level: null, has_verifier: true, status: 'VIOLATED' as const,
        counts: { VERIFIED_IN_SCOPE: 0, VIOLATED: 1, UNKNOWN: 0, REVIEW: 0 }, waived: 1, exempted: true,
        waivers: [{ exception_id: 'E2', expires_on: '2026-12-31', reason: 'r', alternative: '直し方2' }] },
      { rule_id: 'P-SOON', origin: 'team' as const, title: '近い', level: null, has_verifier: true, status: 'VIOLATED' as const,
        counts: { VERIFIED_IN_SCOPE: 0, VIOLATED: 1, UNKNOWN: 0, REVIEW: 0 }, waived: 1, exempted: true,
        waivers: [{ exception_id: 'E1', expires_on: '2026-11-01', reason: 'r', alternative: '直し方1' }] },
    ]
    const reqs = [req('Q', { verdict: 'REVIEW' }), req('K', { verdict: 'UNKNOWN' })]
    expect(nextActions(reqs, policies).map((a) => [a.kind, a.requirementId])).toEqual([
      ['review', 'Q'], ['exempted', 'P-SOON'], ['exempted', 'P-LATE'], ['unknown', 'K'],
    ])
    expect(nextActions([], policies)[0].hint).toBe('免除の期限 2026-11-01 までに直す: 直し方1')
  })
})

describe('nextActions: システム規約の人向けの題', () => {
  it('KNOWLEDGE-LOCK-001 は内部名の題を平易な文言に置き換え、対処を示す', () => {
    const [a] = nextActions([req('KNOWLEDGE-LOCK-001', { title: 'bundle 6', verdict: 'VIOLATED' })])
    expect(a.title).toBe('リポジトリの知識が最新ではありません')
    expect(a.hint).toBe('リポジトリで同期を実行して最新にしてください')
    expect(a.requirementId).toBe('KNOWLEDGE-LOCK-001')
  })

  it('KNOWLEDGE-BINDINGS-001 と通常の要求（題をそのまま使う）', () => {
    const [b, c] = nextActions([
      req('KNOWLEDGE-BINDINGS-001', { verdict: 'REVIEW' }),
      req('SHOP-ERR-001', { title: '在庫が無ければエラー', verdict: 'VIOLATED' }),
    ])
    expect(c.title).toBe('仕様とコードの対応表が変わりました')
    expect(b.title).toBe('在庫が無ければエラー')
    expect(b.hint).toBe('反例を見る')
  })

  it('規約の要確認・違反も同じ区分に並べる（免除中は除く）', () => {
    const policy = (rule_id: string, status: 'REVIEW' | 'VIOLATED', exempted = false) => ({
      rule_id, origin: 'team' as const, title: `題 ${rule_id}`, level: null, has_verifier: true, status,
      counts: { VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 }, waived: 0, exempted, waivers: [],
    })
    const out = nextActions([req('R-1', { verdict: 'VIOLATED' })], [policy('P-R', 'REVIEW'), policy('P-V', 'VIOLATED'), policy('P-X', 'VIOLATED', true)])
    expect(out.map((a) => [a.kind, a.requirementId])).toEqual([
      ['violated', 'R-1'],
      ['violated', 'P-V'],
      ['review', 'P-R'],
    ])
    expect(out[2].title).toBe('題 P-R')
  })
})
