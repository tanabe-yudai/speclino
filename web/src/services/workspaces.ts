import type { WorkspaceMembership } from '@/types/me'
import { paths } from './paths'

/** 今見ているワークスペース。URL の slug があればそれだけ（所属に無ければ null）、無ければ所属の先頭。 */
export function pickWorkspace(
  workspaces: WorkspaceMembership[],
  slug: string | undefined,
): WorkspaceMembership | null {
  if (slug !== undefined) return workspaces.find((w) => w.slug === slug) ?? null
  return workspaces[0] ?? null
}

/** ログイン直後の行き先。所属が 1 つならそのホーム、0 か 2 つ以上なら自分のワークスペースの画面で選ぶ。 */
export function landingPath(workspaces: WorkspaceMembership[]): string {
  return workspaces.length === 1 ? paths.workspace(workspaces[0].slug) : paths.workspaces()
}

/** サーバーと同じ規則（英小文字・数字・ハイフンで 2〜40 文字、端はハイフン以外、連続ハイフンなし）。 */
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/

/** 識別子の入力の誤り。問題が無ければ null。 */
export function workspaceSlugError(slug: string): string | null {
  if (slug === '') return '識別子を入力してください'
  if (!SLUG_PATTERN.test(slug) || slug.includes('--')) {
    return '識別子は英小文字・数字・ハイフンで 2〜40 文字にします（先頭と末尾は英小文字か数字）'
  }
  return null
}
