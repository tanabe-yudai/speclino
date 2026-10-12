/** プロジェクト画面のタブ（`/w/:slug/p/:owner/:repo/:tab`）。知識と検査は第 1 階層の一覧をこのプロジェクトで絞ったもの。 */

import type { ProjectTab } from '@/types/navigation'

export const PROJECT_TABS: ProjectTab[] = ['overview', 'knowledge', 'runs']

/** URL のタブ。無ければ概要、知らないタブは null（旧いタブは legacyRoutes が転送する）。 */
export function parseProjectTab(raw: string | undefined): ProjectTab | null {
  if (raw === undefined) return 'overview'
  return PROJECT_TABS.find((t) => t === raw) ?? null
}
