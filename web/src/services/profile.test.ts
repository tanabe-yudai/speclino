import { describe, expect, it } from 'vitest'
import { parseDisplayName } from './profile'

describe('parseDisplayName', () => {
  it('前後の空白を除き、空なら消す（null）', () => {
    expect(parseDisplayName('  田辺 ')).toEqual({ ok: true, value: '田辺' })
    expect(parseDisplayName('   ')).toEqual({ ok: true, value: null })
  })
  it('50 文字まで（コードポイントで数える）', () => {
    expect(parseDisplayName('あ'.repeat(50)).ok).toBe(true)
    expect(parseDisplayName('😀'.repeat(50)).ok).toBe(true)
    expect(parseDisplayName('あ'.repeat(51)).ok).toBe(false)
  })
  it('@・改行・制御文字・向きを変える文字は拒否する', () => {
    for (const bad of ['boss@example.com', 'a@b', 'bob＠example.com', 'Alice（x）', 'Alice (x)', 'a\nb', 'a\tb', 'evil\u202etxt', 'a\u2028b']) {
      expect(parseDisplayName(bad).ok).toBe(false)
    }
  })
})
