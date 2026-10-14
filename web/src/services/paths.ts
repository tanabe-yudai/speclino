/** 画面のパス。組み立てはここだけで行う。 */

import type { SettingsTab } from '@/types/command'
import type { ArtifactKind } from '@/types/knowledge'
import type { KnowledgeKind, KnowledgeListFilter, ProjectTab, RunFilter } from '@/types/navigation'
import { KIND_SECTION } from './knowledge'

const e = encodeURIComponent
const ws = (slug: string) => `/w/${e(slug)}`
const kn = (slug: string, kind: ArtifactKind) => `${ws(slug)}/knowledge/${KIND_SECTION[kind]}`
/** 種類ごとの画面の区切り（エディタの区切りと同じ名前）。 */
const listSegment = (kind: KnowledgeKind) => (kind === 'policy' ? 'policies' : KIND_SECTION[kind])

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

/** 絞り込みのクエリ（値の無いものは書かない。順は引数の順）。 */
function filterQuery(entries: [string, string | null | undefined][]): string {
  const parts = entries.filter((x): x is [string, string] => !!x[1]).map(([k, v]) => `${k}=${e(v)}`)
  return parts.length > 0 ? `?${parts.join('&')}` : ''
}

function projectBase(slug: string, fullName: string): string {
  const [owner, repo] = fullName.split('/')
  return `${ws(slug)}/p/${e(owner)}/${e(repo)}`
}

export const paths = {
  home: () => '/app',
  /** 自分のワークスペース（所属の一覧と作成）。 */
  workspaces: () => '/workspaces',
  /** ワークスペースのホーム（やること・件数・プロジェクトの健全性）。 */
  workspace: (slug: string) => ws(slug),
  /** プロジェクト（絞り込みの画面）。概要は素のパス。仕様・検査のタブは一覧と同じ絞り込みのクエリを持てる。 */
  project: (slug: string, fullName: string, tab?: ProjectTab, filter?: Partial<KnowledgeListFilter & RunFilter>) => {
    const base = projectBase(slug, fullName)
    if (!tab || tab === 'overview') return base
    const q = tab === 'specs' ? filterQuery([['status', filter?.status]]) : filterQuery([['gate', filter?.gate]])
    return `${base}/${tab}${q}`
  },
  /** 種類ごとの画面（`/w/:slug/specs` など）。リポジトリで絞れるのは仕様だけ。規約は絞り込みを持たない。 */
  knowledgeList: (slug: string, kind: KnowledgeKind, filter?: Partial<KnowledgeListFilter>) => {
    const base = `${ws(slug)}/${listSegment(kind)}`
    if (kind === 'policy') return base
    return `${base}${filterQuery([['project', kind === 'spec' ? filter?.project : null], ['status', filter?.status]])}`
  },
  /** 規約（チーム規約）の画面。 */
  policies: (slug: string) => `${ws(slug)}/policies`,
  knowledgeNew: (slug: string, kind: ArtifactKind, from?: string | null) => `${kn(slug, kind)}/new${query({ from })}`,
  knowledgeItem: (slug: string, kind: ArtifactKind, id: string, q?: KnowledgeQuery) =>
    `${kn(slug, kind)}/${e(id)}${query(q)}`,
  knowledgeRevisions: (slug: string, kind: ArtifactKind, id: string, from?: string | null) =>
    `${kn(slug, kind)}/${e(id)}/revisions${query({ from })}`,
  /** 検査の一覧。 */
  runs: (slug: string, filter?: Partial<RunFilter>) =>
    `${ws(slug)}/runs${filterQuery([['gate', filter?.gate], ['project', filter?.project]])}`,
  /** 検査の詳細。ワークスペースが分からない run（未登録のリポジトリ）は旧い `/runs/:id`。 */
  run: (slug: string | null | undefined, id: string) => (slug ? `${ws(slug)}/runs/${e(id)}` : `/runs/${e(id)}`),
  observability: (slug: string) => `${ws(slug)}/observability`,
  /** 設定。タブを省けばメンバー。 */
  settings: (slug: string, tab?: SettingsTab) => `${ws(slug)}/settings${tab ? `/${tab}` : ''}`,
}
