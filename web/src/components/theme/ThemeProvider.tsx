import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { browserStorage, readThemeChoice, resolveTheme, writeThemeChoice } from '@/services/theme'
import type { ThemeChoice } from '@/types/theme'
import { ThemeContext } from './themeContext'

const darkQuery = () => window.matchMedia('(prefers-color-scheme: dark)')

/** テーマの選択を保持し、<html> の .dark を同期する（初回は index.html のスクリプトが先に付ける）。 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(() => readThemeChoice(browserStorage()))
  const [prefersDark, setPrefersDark] = useState(() => darkQuery().matches)

  useEffect(() => {
    const query = darkQuery()
    const onChange = (e: MediaQueryListEvent) => setPrefersDark(e.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const resolved = resolveTheme(choice, prefersDark)
  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
  }, [resolved])

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next)
    writeThemeChoice(browserStorage(), next)
  }, [])

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
