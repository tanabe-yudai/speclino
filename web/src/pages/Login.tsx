import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'

interface LocationState {
  from?: { pathname: string }
}

export function Login() {
  const { mode, user, error, signInWithPassword, enterDevMode } = useAuth()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (user) {
    const state = location.state as LocationState | null
    return <Navigate to={state?.from?.pathname ?? '/'} replace />
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
    <main className="login-page">
      <div className="login-card">
        <h1>SpecLino</h1>
        <p className="login-subtitle">審査員向け確認画面（読取り専用）</p>

        {mode === 'dev' ? (
          <div className="login-dev">
            <p className="muted">開発モード: Firebase の環境変数が未設定のため、ヘッダ認証で進みます。</p>
            <button type="button" className="button-primary" onClick={enterDevMode}>
              開発モードで入る
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="login-form">
            <label>
              メールアドレス
              <input
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label>
              パスワード
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            {error && (
              <p className="login-error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="button-primary" disabled={submitting}>
              {submitting ? 'ログイン中…' : 'ログイン'}
            </button>
          </form>
        )}
      </div>
    </main>
  )
}
