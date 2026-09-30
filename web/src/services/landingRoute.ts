import type { LandingDecision } from '@/types/landing'

/** `/` を開いたとき: 認証の確認中は待つ、ログイン済みならアプリ、未ログインならランディングページ。 */
export function landingDecision(loading: boolean, signedIn: boolean): LandingDecision {
  if (loading) return 'wait'
  return signedIn ? 'app' : 'landing'
}
