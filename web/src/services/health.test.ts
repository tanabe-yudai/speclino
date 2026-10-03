import { describe, expect, it } from 'vitest'
import { healthPercent, healthRatio, ringSegments } from './health'

describe('healthRatio', () => {
  it('適合の割合', () => {
    expect(healthRatio({ VERIFIED_IN_SCOPE: 3, VIOLATED: 1, UNKNOWN: 0, REVIEW: 0 })).toBe(0.75)
  })
  it('未解析も分母に入れる（抽出できなかった要求を落とさない）', () => {
    expect(healthRatio({ VERIFIED_IN_SCOPE: 1, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 }, 1)).toBe(0.5)
  })
  it('判定が無ければ null（0% と言わない）', () => {
    expect(healthRatio(null)).toBeNull()
    expect(healthRatio({ VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 })).toBeNull()
  })
})

describe('healthPercent', () => {
  it('切り捨てる（違反が 1 件でもあれば 100 と出さない）', () => {
    expect(healthPercent(0.995)).toBe(99)
    expect(healthPercent(1)).toBe(100)
    expect(healthPercent(0)).toBe(0)
  })
})

describe('ringSegments', () => {
  it('適合・要確認・未検証・違反・未解析の順に、件数の割合で円を区切る（0 件は出さない）', () => {
    const segs = ringSegments({ VERIFIED_IN_SCOPE: 6, VIOLATED: 1, UNKNOWN: 0, REVIEW: 2 }, 1)
    expect(segs).toEqual([
      { kind: 'ok', from: 0, to: 60 },
      { kind: 'rev', from: 60, to: 80 },
      { kind: 'bad', from: 80, to: 90 },
      { kind: 'none', from: 90, to: 100 },
    ])
  })
  it('判定が無ければ空', () => {
    expect(ringSegments(null, 0)).toEqual([])
    expect(ringSegments({ VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 }, 0)).toEqual([])
  })
})

