import { useCallback, useEffect, useMemo, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { getMe } from '@/api/me'
import { ApiError } from '@/api/errors'
import { ErrorState } from '@/components/ErrorState'
import { MeContext } from '@/hooks/meContext'
import type { Me } from '@/types/me'
import { CommandPalette } from './CommandPalette'
import { TopBar } from './TopBar'

/** ログイン後の全画面の外枠（上部バー・⌘K）。/api/me はここで 1 回だけ取る。 */
export function AppShell() {
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)

  const reload = useCallback(() => {
    return getMe()
      .then((m) => {
        setMe(m)
        setError(null)
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : '利用者の情報を取得できませんでした'),
      )
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((open) => !open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const value = useMemo(() => ({ me, error, reload }), [me, error, reload])
  return (
    <MeContext.Provider value={value}>
      <TopBar onOpenPalette={() => setPaletteOpen(true)} />
      {/* 長いパスや ID（`::` や `/` を含み空白の無い文字列）でカードが押し広げられないよう、どこでも折り返せるようにする */}
      <main className="mx-auto w-full max-w-6xl px-4 pt-6 pb-16 [overflow-wrap:anywhere] sm:px-6">
        {error && !me ? <ErrorState message={error} onRetry={reload} /> : <Outlet />}
      </main>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </MeContext.Provider>
  )
}
