/** 第 1 階層（ホーム・知識・検査・観測・設定）。上部バー（PC）と画面下のタブ（スマホ）が使う。 */

import type { SectionId } from '@/types/navigation'
import { paths } from './paths'

export const SECTIONS: SectionId[] = ['home', 'knowledge', 'runs', 'observability', 'settings']

export function sectionPath(slug: string, id: SectionId): string {
  switch (id) {
    case 'home':
      return paths.workspace(slug)
    case 'knowledge':
      return paths.knowledge(slug)
    case 'runs':
      return paths.runs(slug)
    case 'observability':
      return paths.observability(slug)
    case 'settings':
      return paths.settings(slug)
  }
}

const SECTION_SEGMENT: Record<string, SectionId> = {
  knowledge: 'knowledge',
  runs: 'runs',
  observability: 'observability',
  settings: 'settings',
}

/** 今の画面がどの第 1 階層の下か。プロジェクト（ホームのカードから入る）はホーム。 */
export function currentSection(pathname: string): SectionId | null {
  if (/^\/runs\//.test(pathname)) return 'runs'
  const m = /^\/w\/[^/]+(?:\/([^/]+))?/.exec(pathname)
  if (!m) return null
  if (!m[1] || m[1] === 'p') return 'home'
  return SECTION_SEGMENT[m[1]] ?? null
}
