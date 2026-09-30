import { describe, expect, it } from 'vitest'
import { verdictTone } from './verdictTone'

describe('verdictTone', () => {
  it('4 値を固定の色に対応させる', () => {
    expect(verdictTone('VERIFIED_IN_SCOPE')).toBe('ok')
    expect(verdictTone('VIOLATED')).toBe('bad')
    expect(verdictTone('UNKNOWN')).toBe('unk')
    expect(verdictTone('REVIEW')).toBe('rev')
  })
})
