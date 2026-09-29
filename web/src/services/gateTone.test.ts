import { describe, expect, it } from 'vitest'
import { gateTone } from './labels'

describe('gateTone', () => {
  it('ゲートの結果を判定の色に対応させる', () => {
    expect(gateTone('pass')).toBe('ok')
    expect(gateTone('pass_with_exceptions')).toBe('rev')
    expect(gateTone('fail')).toBe('bad')
  })
})
