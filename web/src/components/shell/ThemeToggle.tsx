import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/components/theme/useTheme'

/** ライト / ダークを入れ替える（OS に合わせる選択は AccountMenu の中）。 */
export function ThemeToggle() {
  const { resolved, setChoice } = useTheme()
  const next = resolved === 'dark' ? 'light' : 'dark'
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={next === 'dark' ? 'ダークにする' : 'ライトにする'}
      onClick={() => setChoice(next)}
    >
      {resolved === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  )
}
