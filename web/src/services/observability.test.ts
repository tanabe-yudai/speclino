import { describe, expect, it } from 'vitest'
import { fetchLabel, fetchLag, formatRatio, lagLabel, lagTone, unknownEntries } from './observability'

describe('formatRatio', () => {
  it('測っていないものは「未計測」で、0% にしない', () => {
    expect(formatRatio(null)).toBe('未計測')
    expect(formatRatio({ numerator: 2, denominator: 3, rate: 2 / 3 })).toBe('67%（2/3）')
    expect(formatRatio({ numerator: 0, denominator: 4, rate: 0 })).toBe('0%（0/4）')
  })
})

describe('lagLabel / lagTone', () => {
  it('遅れの表示', () => {
    expect(lagLabel(null)).toBe('未計測')
    expect(lagLabel(0)).toBe('最新')
    expect(lagLabel(2)).toBe('古い（2 回前の承認）')
    expect(lagTone(0)).toBe('ok')
    expect(lagTone(1)).toBe('rev')
    expect(lagTone(null)).toBe('unk')
  })
})

describe('unknownEntries', () => {
  it('件数のある理由だけを多い順に', () => {
    const entries = unknownEntries({ unbound: 1, inexact_path: 3, analysis_limit: 0, missing_finding: 0, lock: 1, other: 0 })
    expect(entries.map((e) => e.reason)).toEqual(['inexact_path', 'unbound', 'lock'])
    expect(entries[0].label).toBe('不完全な経路')
  })
})

describe('fetchLabel', () => {
  it('トークン名、無ければメンバー', () => {
    const base = { revoked: false, channel: 'sync', token_id: null, uid: null, member_email: null, project_id: null, repo_full_name: null, last_fetched_at: null, last_generation: null, behind_by: null }
    expect(fetchLabel({ ...base, token_name: 'ci' })).toBe('ci')
    expect(fetchLabel({ ...base, token_name: null, uid: 'u1', member_email: 'a@example.com' })).toBe('a@example.com')
    expect(fetchLabel({ ...base, token_name: null, uid: 'u1' })).toBe('メンバー')
  })
})

describe('fetchLag', () => {
  it('失効済みは遅れに数えず「失効済み」', () => {
    const base = { channel: 'sync', token_id: 't', token_name: 'old', uid: null, member_email: null, project_id: null, repo_full_name: null, last_fetched_at: '2026-10-01T00:00:00Z', last_generation: 1 }
    expect(fetchLag({ ...base, revoked: true, behind_by: null })).toEqual({ label: '失効済み', tone: 'exempt' })
    expect(fetchLag({ ...base, revoked: false, behind_by: 1 })).toEqual({ label: '古い（1 回前の承認）', tone: 'rev' })
  })
})
