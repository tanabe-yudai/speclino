/**
 * 旧いパス（ブックマーク・PR コメントのリンク）を新しい画面へ（design 4 章）。
 * 転送の表はこの関数 1 つにまとめる（画面側は結果へ Navigate するだけ）。
 */

import type { SettingsTab } from '@/types/command'
import type { KnowledgeKind } from '@/types/navigation'
import { KNOWLEDGE_KINDS, parseKnowledgeListFilter } from './knowledgeNav'
import { paths } from './paths'

const SECTION_KIND: Record<string, KnowledgeKind> = {
  specs: 'spec',
  skills: 'skill',
  instructions: 'instructions',
  policies: 'policy',
}

/** 設定の旧い `?tab=` と、今のタブ（同じ名前は転送しない）。 */
const SETTINGS_TAB: Record<string, SettingsTab> = {
  members: 'members',
  repositories: 'repositories',
  agents: 'agents',
  integrations: 'agents',
  sync: 'agents',
  tokens: 'agents',
  project: 'projects',
  projects: 'projects',
  retired: 'retired',
}

function decode(segments: string[]): string[] | null {
  try {
    return segments.map((s) => decodeURIComponent(s))
  } catch {
    return null
  }
}

function workspaceTab(slug: string, tab: string): string {
  switch (tab) {
    case 'shared':
      return paths.knowledgeList(slug, 'skill')
    case 'policies':
      return paths.policies(slug)
    case 'usage':
      return paths.observability(slug)
    default:
      return paths.workspace(slug)
  }
}

/**
 * 旧い統合一覧（`/w/:slug/knowledge?kind=&project=&status=`）を種類の画面へ。種類が無ければ仕様。
 * 取り下げ済みで絞っていたものは設定の「取り下げ済みの知識」へ。
 */
function knowledgeList(slug: string, q: URLSearchParams): string {
  if (q.get('status') === 'retired') return paths.settings(slug, 'retired')
  const kind = KNOWLEDGE_KINDS.find((k) => k === q.get('kind')) ?? 'spec'
  return paths.knowledgeList(slug, kind, parseKnowledgeListFilter(q))
}

const RULES: { re: RegExp; to: (m: string[], params: URLSearchParams) => string | null }[] = [
  {
    re: /^\/workspaces\/([^/]+)\/(mcp-tokens|repositories)\/?$/,
    to: ([slug, page]) => paths.settings(slug, page === 'mcp-tokens' ? 'agents' : 'repositories'),
  },
  { re: /^\/w\/([^/]+)\/?$/, to: ([slug], q) => (q.has('tab') ? workspaceTab(slug, q.get('tab') ?? '') : null) },
  { re: /^\/w\/([^/]+)\/knowledge\/?$/, to: ([slug], q) => knowledgeList(slug, q) },
  {
    // 種類ごとの旧い一覧（規約の画面 knowledge/policies を含む）は種類の画面へ、知らない区切りは仕様へ
    re: /^\/w\/([^/]+)\/knowledge\/([^/]+)\/?$/,
    to: ([slug, section]) => paths.knowledgeList(slug, SECTION_KIND[section] ?? 'spec'),
  },
  {
    re: /^\/w\/([^/]+)\/settings\/?$/,
    to: ([slug], q) => (q.has('tab') ? paths.settings(slug, SETTINGS_TAB[q.get('tab') ?? '']) : null),
  },
  { re: /^\/w\/([^/]+)\/settings\/([^/]+)\/?$/, to: ([slug, tab]) => paths.settings(slug, SETTINGS_TAB[tab]) },
  {
    // プロジェクトの旧いタブ: 知識のタブと仕様は仕様のタブへ、スキル・指示書・規約はワークスペースの種類の画面へ
    re: /^\/w\/([^/]+)\/p\/([^/]+)\/([^/]+)\/([^/]+)\/?$/,
    to: ([slug, owner, repo, tab], q) => {
      if (tab === 'specs' || tab === 'runs') return null
      const fullName = `${owner}/${repo}`
      const kind = tab === 'knowledge' ? (KNOWLEDGE_KINDS.find((k) => k === q.get('kind')) ?? 'spec') : SECTION_KIND[tab]
      if (!kind) return paths.project(slug, fullName)
      const filter = tab === 'knowledge' ? parseKnowledgeListFilter(q) : undefined
      return kind === 'spec' ? paths.project(slug, fullName, 'specs', filter) : paths.knowledgeList(slug, kind, filter)
    },
  },
]

/**
 * 旧いパスなら新しいパス、そうでなければ null。search は `?tab=` などの旧いクエリ。
 * 壊れた % を含むパスは例外にせず null。
 */
export function legacyRedirect(pathname: string, search = ''): string | null {
  const params = new URLSearchParams(search)
  for (const rule of RULES) {
    const m = rule.re.exec(pathname)
    if (!m) continue
    const parts = decode(m.slice(1))
    if (!parts) return null
    const to = rule.to(parts, params)
    return to === `${pathname}${search}` ? null : to
  }
  return null
}
