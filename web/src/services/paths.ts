/** 画面のパス（design 4 章）。組み立てはここだけで行う。 */

import type { SettingsTab } from '@/types/command'
import type { ArtifactKind } from '@/types/knowledge'
import type { ProjectTab, WorkspaceTab } from '@/types/navigation'
import { KIND_SECTION } from './knowledge'

const e = encodeURIComponent
const kn = (slug: string, kind: ArtifactKind) => `/w/${e(slug)}/knowledge/${KIND_SECTION[kind]}`

/** エディタ・改訂履歴へ渡すクエリ。`from` は戻り先のプロジェクト（owner/repo）、`chat` は会話の ID（`new` なら新規）。 */
export interface KnowledgeQuery {
  from?: string | null
  chat?: string | null
}

function query(q?: KnowledgeQuery): string {
  const parts: string[] = []
  if (q?.chat) parts.push(`chat=${e(q.chat)}`)
  if (q?.from) parts.push(`from=${e(q.from)}`)
  return parts.length > 0 ? `?${parts.join('&')}` : ''
}

export const paths = {
  home: () => '/app',
  /** ワークスペースのホーム。tab を省く（またはプロジェクト）なら素のパス。 */
  workspace: (slug: string, tab?: WorkspaceTab) =>
    `/w/${e(slug)}${tab && tab !== 'projects' ? `?tab=${tab}` : ''}`,
  /** プロジェクト。tab を省く（または概要）なら素のパス。 */
  project: (slug: string, fullName: string, tab?: ProjectTab) => {
    const [owner, repo] = fullName.split('/')
    return `/w/${e(slug)}/p/${e(owner)}/${e(repo)}${tab && tab !== 'overview' ? `/${tab}` : ''}`
  },
  policies: (slug: string) => `/w/${e(slug)}/policies`,
  knowledgeNew: (slug: string, kind: ArtifactKind, from?: string | null) => `${kn(slug, kind)}/new${query({ from })}`,
  knowledgeItem: (slug: string, kind: ArtifactKind, id: string, q?: KnowledgeQuery) =>
    `${kn(slug, kind)}/${e(id)}${query(q)}`,
  knowledgeRevisions: (slug: string, kind: ArtifactKind, id: string, from?: string | null) =>
    `${kn(slug, kind)}/${e(id)}/revisions${query({ from })}`,
  observability: (slug: string) => `/w/${e(slug)}/observability`,
  settings: (slug: string, tab?: SettingsTab) => `/w/${e(slug)}/settings${tab ? `?tab=${tab}` : ''}`,
  run: (id: string) => `/runs/${e(id)}`,
}
