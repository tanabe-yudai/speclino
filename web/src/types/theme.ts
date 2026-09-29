/** テーマ。system は OS の設定（prefers-color-scheme）に従う。 */
export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export interface ThemeStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}
