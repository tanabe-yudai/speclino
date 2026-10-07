/** スマホ幅のメニュー（ハンバーガー）の項目。ワークスペースのホーム → プロジェクト（今いるものはタブも）→ チーム共通・規約・利用状況・設定。 */

import type { NavEntry } from '@/types/navigation'
import type { ProjectSummary } from '@/types/project'
import { paths } from './paths'
import { PROJECT_TABS, PROJECT_TAB_LABELS } from './projectTabs'
import { WORKSPACE_TAB_LABELS } from './workspaceTabs'

export function mobileNavEntries(input: {
  slug: string
  projects: ProjectSummary[]
  /** 今開いているプロジェクト（owner/repo）。あればそのタブを並べる */
  currentRepo: string | null
}): NavEntry[] {
  const { slug, projects, currentRepo } = input
  const entries: NavEntry[] = [{ label: 'ワークスペースのホーム', to: paths.workspace(slug), depth: 0 }]
  for (const p of projects) {
    const [, repo] = p.repo_full_name.split('/')
    entries.push({ label: repo, to: paths.project(slug, p.repo_full_name), depth: 0, project: true })
    if (p.repo_full_name === currentRepo) {
      // 概要はプロジェクトの行そのもの
      for (const t of PROJECT_TABS.filter((x) => x !== 'overview')) {
        entries.push({ label: PROJECT_TAB_LABELS[t], to: paths.project(slug, p.repo_full_name, t), depth: 1 })
      }
    }
  }
  entries.push(
    { label: WORKSPACE_TAB_LABELS.shared, to: paths.workspace(slug, 'shared'), depth: 0 },
    { label: 'チーム規約', to: paths.workspace(slug, 'policies'), depth: 0 },
    { label: WORKSPACE_TAB_LABELS.usage, to: paths.workspace(slug, 'usage'), depth: 0 },
    { label: '設定', to: paths.settings(slug), depth: 0 },
  )
  return entries
}

/** メニューの項目が今の画面か（パスとクエリが一致）。プロジェクトの概要は素のパスで、タブ付きの子も別項目として扱う。 */
export function isCurrentEntry(to: string, pathname: string, search: string): boolean {
  return to === `${pathname}${search}`
}
