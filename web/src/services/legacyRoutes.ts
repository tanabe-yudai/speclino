/** 旧いパス（ブックマーク）を新しい画面へ（design 4 章）。 */

import { paths } from './paths'

const LEGACY = /^\/workspaces\/([^/]+)\/(mcp-tokens|repositories)\/?$/

export function legacyRedirect(pathname: string): string | null {
  const m = LEGACY.exec(pathname)
  if (!m) return null
  const slug = decodeURIComponent(m[1])
  return paths.settings(slug, m[2] === 'mcp-tokens' ? 'tokens' : 'repositories')
}
