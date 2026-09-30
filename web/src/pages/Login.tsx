import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { loginReturnPath } from '@/services/loginReturn'
import type { LoginFrom } from '@/types/auth'
import { Panel } from '@/components/app/Panel'
import { ThemeToggle } from '@/components/shell/ThemeToggle'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface LocationState {
  from?: LoginFrom
}

export function Login() {
  const { mode, user, error, signInWithPassword, enterDevMode } = useAuth()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (user) {
    const state = location.state as LocationState | null
    return <Navigate to={loginReturnPath(state?.from)} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await signInWithPassword(email, password)
    } catch {
      // エラーメッセージは AuthProvider 側の state（error）で表示する。
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="relative grid min-h-screen place-items-center px-4">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <Panel className="w-full max-w-sm space-y-6 p-8 shadow-lift">
        <div className="space-y-2">
          <p className="font-extrabold tracking-tight">
            <span className="text-brand">◆</span> SpecLino
          </p>
          <h1 className="text-2xl font-bold tracking-tight">
            <span className="text-brand">仕様どおりか</span>、確かめる。
          </h1>
        </div>

        {mode === 'dev' ? (
          <div className="space-y-3">
            <p className="text-sm text-ink-2">開発モード: Firebase の環境変数が未設定のため、ヘッダ認証で進みます。</p>
            <Button variant="secondary" className="w-full" onClick={enterDevMode}>
              開発モードで入る
            </Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid gap-1.5">
              <Label htmlFor="email">メールアドレス</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="password">パスワード</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error && (
              <p className="text-sm text-bad" role="alert">
                {error}
              </p>
            )}
            <Button type="submit" disabled={submitting} className="w-full bg-brand text-white">
              {submitting ? 'ログインしています…' : 'ログイン'}
            </Button>
          </form>
        )}
      </Panel>
    </main>
  )
}
