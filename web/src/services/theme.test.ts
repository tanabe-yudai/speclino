import { describe, expect, it } from 'vitest'
import { THEME_STORAGE_KEY, readThemeChoice, resolveTheme, writeThemeChoice } from './theme'

const memory = (init: Record<string, string> = {}) => {
  const data = { ...init }
  return {
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v
    },
    data,
  }
}
const broken = {
  getItem: (): string | null => {
    throw new Error('SecurityError')
  },
  setItem: () => {
    throw new Error('QuotaExceededError')
  },
}

describe('resolveTheme', () => {
  it('system は OS の設定に従う', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })
  it('手動の選択は OS より優先する', () => {
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })
})

describe('readThemeChoice / writeThemeChoice', () => {
  it('保存した選択を読み戻す', () => {
    const s = memory()
    writeThemeChoice(s, 'dark')
    expect(s.data[THEME_STORAGE_KEY]).toBe('dark')
    expect(readThemeChoice(s)).toBe('dark')
  })
  it('知らない値・未保存は system', () => {
    expect(readThemeChoice(memory({ [THEME_STORAGE_KEY]: 'sepia' }))).toBe('system')
    expect(readThemeChoice(memory())).toBe('system')
  })
  it('storage が例外を投げても落ちずに system を返し、保存は黙って諦める', () => {
    expect(readThemeChoice(broken)).toBe('system')
    expect(() => writeThemeChoice(broken, 'dark')).not.toThrow()
  })
  it('storage 自体が無い（アクセスで例外）なら system、保存は何もしない', () => {
    expect(readThemeChoice(null)).toBe('system')
    expect(() => writeThemeChoice(null, 'dark')).not.toThrow()
  })
})
