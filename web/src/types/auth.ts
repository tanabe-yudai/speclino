/** 認証状態の型。Firebase（本番）と開発モード（dev）の両方を同じ形で扱う。 */

export type AuthMode = 'firebase' | 'dev'

export interface AuthUser {
  uid: string
  email: string | null
}

export interface AuthContextValue {
  /** Firebase の環境変数が揃っていれば 'firebase'、無ければ 'dev'。 */
  mode: AuthMode
  user: AuthUser | null
  /** 初回の認証状態解決中（Firebase の onAuthStateChanged 初回コールバック待ち）。 */
  loading: boolean
  /** 直近のログイン操作で発生したエラーメッセージ（日本語）。 */
  error: string | null
  signInWithPassword: (email: string, password: string) => Promise<void>
  enterDevMode: () => void
  signOut: () => Promise<void>
}

/** ログイン画面へ転送される前にいた場所（react-router の location の一部）。 */
export interface LoginFrom {
  pathname: string
  search?: string
  /** 招待リンクのトークンはフラグメントに載るので、これも持ち越す。 */
  hash?: string
}
