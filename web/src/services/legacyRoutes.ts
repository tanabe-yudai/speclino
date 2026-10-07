/** 旧いパス（ブックマーク）を新しい画面へ（design 4 章）。 */

import { parseSection } from './knowledge'
import { paths } from './paths'

const LEGACY = /^\/workspaces\/([^/]+)\/(mcp-tokens|repositories)\/?$/

export function legacyRedirect(pathname: string): string | null {
  const m = LEGACY.exec(pathname)
  if (!m) return null
  let slug: string
  try {
    slug = decodeURIComponent(m[1])
  } catch {
    return null
  }
  return paths.settings(slug, m[2] === 'mcp-tokens' ? 'tokens' : 'repositories')
}

/**
 * S74: 知識の一覧（`/w/:slug/knowledge[/:section]`）は無くなった。
 * スキル・指示書はチーム共通のタブへ、仕様（と不明な区分）はワークスペースのホームへ。
 */
export function knowledgeListRedirect(slug: string, section: string | undefined): string {
  const kind = parseSection(section)
  return kind === 'skill' || kind === 'instructions' ? paths.workspace(slug, 'shared') : paths.workspace(slug)
}
