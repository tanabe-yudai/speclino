/** テーマの決定と保存。保存先が使えない（プライベートブラウズ等）ときは OS に従う。 */

import type { ResolvedTheme, ThemeChoice, ThemeStorage } from '@/types/theme'

export const THEME_STORAGE_KEY = 'speclino.theme'
const CHOICES: readonly string[] = ['light', 'dark', 'system']

export function resolveTheme(choice: ThemeChoice, prefersDark: boolean): ResolvedTheme {
  if (choice === 'system') return prefersDark ? 'dark' : 'light'
  return choice
}

/** storage が null（アクセス自体が例外になる環境）でも、読めなければ system。 */
export function readThemeChoice(storage: ThemeStorage | null): ThemeChoice {
  try {
    const value = storage?.getItem(THEME_STORAGE_KEY) ?? null
    return value !== null && CHOICES.includes(value) ? (value as ThemeChoice) : 'system'
  } catch {
    return 'system'
  }
}

export function writeThemeChoice(storage: ThemeStorage | null, choice: ThemeChoice): void {
  try {
    storage?.setItem(THEME_STORAGE_KEY, choice)
  } catch {
    // 保存できなくても、この画面の中では切り替えは効く
  }
}

/** window.localStorage へのアクセス自体が例外を投げる環境があるので、その場合は null。 */
export function browserStorage(): ThemeStorage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}
