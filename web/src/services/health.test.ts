import { describe, expect, it } from 'vitest'
import { healthPercent, ringSegments, STATE_META, statusRatio } from './health'

const c = (over: Partial<Record<'violated' | 'review' | 'unknown' | 'exempted' | 'ok', number>>) => ({
  violated: 0, review: 0, unknown: 0, exempted: 0, ok: 0, ...over,
})

describe('statusRatio', () => {
  it('要求と規約を合わせた全体のうち適合の割合', () => {
    expect(statusRatio(c({ ok: 27, exempted: 1 }))).toBe(27 / 28)
  })
  it('判定が無ければ null（0% と言わない）', () => {
    expect(statusRatio(null)).toBeNull()
    expect(statusRatio(c({}))).toBeNull()
  })
})

describe('healthPercent', () => {
  it('切り捨てる（問題が 1 件でもあれば 100 と出さない）', () => {
    expect(healthPercent(0.995)).toBe(99)
    expect(healthPercent(1)).toBe(100)
    expect(healthPercent(0)).toBe(0)
  })
})

describe('ringSegments', () => {
  it('適合 → 免除中 → 未検証 → 要確認 → 違反 の順に、件数の割合で区切る（0 件は出さない）', () => {
    expect(ringSegments(c({ ok: 6, exempted: 1, review: 2, violated: 1 }))).toEqual([
      { kind: 'ok', from: 0, to: 60 },
      { kind: 'exempted', from: 60, to: 70 },
      { kind: 'review', from: 70, to: 90 },
      { kind: 'violated', from: 90, to: 100 },
    ])
  })
  it('判定が無ければ空', () => {
    expect(ringSegments(null)).toEqual([])
  })
})

describe('STATE_META', () => {
  it('状態の見出しと円の区分は同じ名前と色', () => {
    expect(STATE_META.exempted).toEqual({ label: '免除中あり', segment: '免除中', tone: 'exempt' })
    expect(STATE_META.ok.label).toBe('健全')
  })
})
