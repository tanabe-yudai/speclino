import { describe, expect, it } from 'vitest'
import { revealedCount } from './reveal'

describe('revealedCount', () => {
  it('開いた直後は 0 件', () => {
    expect(revealedCount(0, 900, 3, false)).toBe(0)
  })
  it('経過時間に応じて 1 件ずつ確定する', () => {
    expect(revealedCount(899, 900, 3, false)).toBe(0)
    expect(revealedCount(900, 900, 3, false)).toBe(1)
    expect(revealedCount(2000, 900, 3, false)).toBe(2)
  })
  it('全部確定したらそのまま', () => {
    expect(revealedCount(99_000, 900, 3, false)).toBe(3)
  })
  it('動きを減らす設定では最初から全部', () => {
    expect(revealedCount(0, 900, 3, true)).toBe(3)
  })
})
