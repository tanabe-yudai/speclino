/**
 * 旧いパス（ブックマーク・PR コメントのリンク）を新しい画面へ（design 4 章）。
 * 転送の表はこの関数 1 つにまとめる（画面側は結果へ Navigate するだけ）。
 */

import type { SettingsTab } from '@/types/command'
import type { KnowledgeKind } from '@/types/navigation'
import { paths } from './paths'

const SECTION_KIND: Record<string, KnowledgeKind> = {
  specs: 'spec',
  skills: 'skill',
  instructions: 'instructions',
  policies: 'policy',
}

/** 設定の旧い `?tab=`。 */
const SETTINGS_TAB: Record<string, SettingsTab> = {
  members: 'members',
  repositories: 'repositories',
  agents: 'agents',
  integrations: 'agents',
  sync: 'agents',
  tokens: 'agents',
  project: 'projects',
  projects: 'projects',
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
      return paths.knowledge(slug)
    case 'policies':
      return paths.policies(slug)
    case 'usage':
      return paths.observability(slug)
    default:
      return paths.workspace(slug)
  }
}

const RULES: { re: RegExp; to: (m: string[], params: URLSearchParams) => string | null }[] = [
  {
    re: /^\/workspaces\/([^/]+)\/(mcp-tokens|repositories)\/?$/,
    to: ([slug, page]) => paths.settings(slug, page === 'mcp-tokens' ? 'agents' : 'repositories'),
  },
  { re: /^\/w\/([^/]+)\/?$/, to: ([slug], q) => (q.has('tab') ? workspaceTab(slug, q.get('tab') ?? '') : null) },
  { re: /^\/w\/([^/]+)\/policies\/?$/, to: ([slug]) => paths.policies(slug) },
  {
    // 種類ごとの一覧は、知識の一覧を種類で絞ったものへ（規約の画面 knowledge/policies は別のルート）
    re: /^\/w\/([^/]+)\/knowledge\/([^/]+)\/?$/,
    to: ([slug, section]) => {
      const kind = SECTION_KIND[section]
      return kind === 'policy' ? null : paths.knowledge(slug, kind ? { kind } : undefined)
    },
  },
  {
    re: /^\/w\/([^/]+)\/settings\/?$/,
    to: ([slug], q) => (q.has('tab') ? paths.settings(slug, SETTINGS_TAB[q.get('tab') ?? '']) : null),
  },
  { re: /^\/w\/([^/]+)\/settings\/([^/]+)\/?$/, to: ([slug, tab]) => paths.settings(slug, SETTINGS_TAB[tab]) },
  {
    // プロジェクトの旧いタブ（仕様・スキル・指示書・規約）は、知識のタブを種類で絞ったものへ
    re: /^\/w\/([^/]+)\/p\/([^/]+)\/([^/]+)\/([^/]+)\/?$/,
    to: ([slug, owner, repo, tab]) => {
      if (tab === 'knowledge' || tab === 'runs') return null
      const kind = SECTION_KIND[tab]
      const fullName = `${owner}/${repo}`
      return kind ? paths.project(slug, fullName, 'knowledge', { kind }) : paths.project(slug, fullName)
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
