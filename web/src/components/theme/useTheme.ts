import { useContext } from 'react'
import { ThemeContext, type ThemeContextValue } from './themeContext'

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme は ThemeProvider の中で使う')
  return value
}
