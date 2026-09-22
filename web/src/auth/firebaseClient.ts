/**
 * Firebase JS SDK（Identity Platform 互換）でのメール/パスワードログイン。
 * 環境変数が無ければ isFirebaseConfigured=false になり、AuthProvider は dev モードへ切り替える。
 */

import { initializeApp, type FirebaseApp } from 'firebase/app'
import {
  getAuth,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signOut,
  type Auth,
  type User,
} from 'firebase/auth'

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
}

export const isFirebaseConfigured = Boolean(
  firebaseConfig.apiKey && firebaseConfig.authDomain && firebaseConfig.projectId,
)

let app: FirebaseApp | null = null
let authInstance: Auth | null = null

function getFirebaseAuth(): Auth {
  if (!isFirebaseConfigured) {
    throw new Error('Firebase の環境変数（VITE_FIREBASE_*）が設定されていません')
  }
  app ??= initializeApp(firebaseConfig)
  authInstance ??= getAuth(app)
  return authInstance
}

export async function firebaseSignIn(email: string, password: string): Promise<User> {
  const auth = getFirebaseAuth()
  const credential = await signInWithEmailAndPassword(auth, email, password)
  return credential.user
}

export async function firebaseSignOut(): Promise<void> {
  if (!authInstance) return
  await signOut(authInstance)
}

export function subscribeAuthState(callback: (user: User | null) => void): () => void {
  const auth = getFirebaseAuth()
  return onAuthStateChanged(auth, callback)
}

/** 現在ログイン中のユーザーの ID トークン。未ログインなら null。 */
export async function getCurrentIdToken(): Promise<string | null> {
  if (!isFirebaseConfigured || !authInstance?.currentUser) return null
  return authInstance.currentUser.getIdToken()
}

/** signInWithEmailAndPassword が投げる FirebaseError を日本語メッセージへ変換する。 */
export function describeFirebaseAuthError(error: unknown): string {
  const code = typeof error === 'object' && error !== null && 'code' in error ? String(error.code) : ''
  switch (code) {
    case 'auth/invalid-email':
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
    case 'auth/user-not-found':
      return 'メールアドレスまたはパスワードが正しくありません'
    case 'auth/too-many-requests':
      return '試行回数が多すぎます。しばらく待ってから再度お試しください'
    case 'auth/network-request-failed':
      return 'ネットワークエラーが発生しました'
    default:
      return error instanceof Error ? error.message : 'ログインに失敗しました'
  }
}
