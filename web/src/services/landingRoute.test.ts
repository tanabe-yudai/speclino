import { describe, expect, it } from 'vitest'
import { landingDecision } from './landingRoute'

describe('landingDecision', () => {
  it('認証の確認中は何も描かない（ログイン済みの人にランディングページを一瞬も見せない）', () => {
    expect(landingDecision(true, false)).toBe('wait')
    expect(landingDecision(true, true)).toBe('wait')
  })
  it('ログイン済みならアプリへ', () => {
    expect(landingDecision(false, true)).toBe('app')
  })
  it('未ログインならランディングページ', () => {
    expect(landingDecision(false, false)).toBe('landing')
  })
})
