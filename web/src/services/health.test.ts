import { describe, expect, it } from 'vitest'
import { healthPercent, healthRatio, healthTone } from './health'

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

describe('healthTone', () => {
  it('リングの色はゲートの結果に合わせる（要求が 100% でも例外付き通過なら緑にしない）', () => {
    expect(healthTone('pass')).toBe('ok')
    expect(healthTone('pass_with_exceptions')).toBe('rev')
    expect(healthTone('fail')).toBe('bad')
    expect(healthTone(null)).toBe('ok')
  })
})
