import { describe, expect, it } from 'vitest'
import { runRowLabel, runTargetLabel } from './format'

describe('runTargetLabel', () => {
  it('PR があれば番号、無ければ手動', () => {
    expect(runTargetLabel(7)).toBe('PR #7')
    expect(runTargetLabel(null)).toBe('手動')
    expect(runTargetLabel(undefined)).toBe('手動')
  })
})

describe('runRowLabel', () => {
  it('owner を省いたリポジトリ名と対象', () => {
    expect(runRowLabel('example/shop', 7)).toBe('shop PR #7')
    expect(runRowLabel('example/shop', null)).toBe('shop 手動')
  })
  it('リポジトリが分からなければ明示する', () => {
    expect(runRowLabel(null, 3)).toBe('(リポジトリ不明) PR #3')
  })
})
