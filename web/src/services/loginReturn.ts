import type { LoginFrom } from '@/types/auth'

/** ログイン後に戻す先。設定のタブなどクエリで持つ状態を落とさない。 */
export function loginReturnPath(from: LoginFrom | undefined): string {
  if (!from) return '/app'
  return from.pathname + (from.search ?? '')
}
