import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AuthContext } from './AuthContext'
import type { AuthMode, AuthUser } from '../types/auth'
import { clearDevSession, isDevSessionActive, markDevSessionActive } from './devAuth'
import {
  describeFirebaseAuthError,
  firebaseSignIn,
  firebaseSignOut,
  isFirebaseConfigured,
  subscribeAuthState,
} from './firebaseClient'

interface AuthProviderProps {
  children: ReactNode
}

/**
 * Firebase の環境変数が揃っていれば 'firebase' モード、揃っていなければ 'dev' モードで動く。
 * dev モードでも自動ログインはせず、Login 画面のボタンでセッション内フラグを立てる。
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const mode: AuthMode = isFirebaseConfigured ? 'firebase' : 'dev'
  // dev モードの初期状態はセッションストレージから同期的に決まるため、レンダー時に直接求める
  // （effect での setState を避け、無駄な再レンダーを1回減らす）。
  const [user, setUser] = useState<AuthUser | null>(() =>
    mode === 'dev' && isDevSessionActive() ? { uid: 'dev-judge', email: null } : null,
  )
  const [loading, setLoading] = useState(mode === 'firebase')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // firebase モードのみ、外部システム（Firebase Auth）の状態変化を購読する。
    if (mode !== 'firebase') return
    const unsubscribe = subscribeAuthState((firebaseUser) => {
      setUser(firebaseUser ? { uid: firebaseUser.uid, email: firebaseUser.email } : null)
      setLoading(false)
    })
    return unsubscribe
  }, [mode])

  const signInWithPassword = useCallback(async (email: string, password: string) => {
    setError(null)
    try {
      const firebaseUser = await firebaseSignIn(email, password)
      setUser({ uid: firebaseUser.uid, email: firebaseUser.email })
    } catch (err) {
      setError(describeFirebaseAuthError(err))
      throw err
    }
  }, [])

  const enterDevMode = useCallback(() => {
    markDevSessionActive()
    setUser({ uid: 'dev-judge', email: null })
  }, [])

  const signOut = useCallback(async () => {
    if (mode === 'firebase') {
      await firebaseSignOut()
    } else {
      clearDevSession()
    }
    setUser(null)
  }, [mode])

  const value = useMemo(
    () => ({ mode, user, loading, error, signInWithPassword, enterDevMode, signOut }),
    [mode, user, loading, error, signInWithPassword, enterDevMode, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
