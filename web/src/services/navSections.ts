/**
 * 第 1 階層（ホーム・仕様・スキル・指示書・規約・検査・観測・設定）。上部バー（PC）はすべてを並べ、
 * 画面下のタブ（スマホ）は BOTTOM_TABS を並べて残り（MORE_SECTIONS）を「その他」に入れる。
 */

import type { KnowledgeKind, SectionId } from '@/types/navigation'
import { paths } from './paths'

export const SECTIONS: SectionId[] = ['home', 'specs', 'skills', 'instructions', 'policies', 'runs', 'observability', 'settings']

/** スマホの画面下のタブ（5 つ目は「その他」）。 */
export const BOTTOM_TABS: SectionId[] = ['home', 'specs', 'runs', 'observability']

/** スマホの「その他」に入れる第 1 階層（ワークスペースの切り替えはこの後ろ）。 */
export const MORE_SECTIONS: SectionId[] = SECTIONS.filter((s) => !BOTTOM_TABS.includes(s))

/** 知識の種類の画面。 */
export const KIND_SECTION_ID: Record<KnowledgeKind, SectionId> = {
  spec: 'specs',
  skill: 'skills',
  instructions: 'instructions',
  policy: 'policies',
}

export function sectionPath(slug: string, id: SectionId): string {
  switch (id) {
    case 'home':
      return paths.workspace(slug)
    case 'specs':
      return paths.knowledgeList(slug, 'spec')
    case 'skills':
      return paths.knowledgeList(slug, 'skill')
    case 'instructions':
      return paths.knowledgeList(slug, 'instructions')
    case 'policies':
      return paths.policies(slug)
    case 'runs':
      return paths.runs(slug)
    case 'observability':
      return paths.observability(slug)
    case 'settings':
      return paths.settings(slug)
  }
}

const SECTION_SEGMENT: Record<string, SectionId> = {
  specs: 'specs',
  skills: 'skills',
  instructions: 'instructions',
  policies: 'policies',
  runs: 'runs',
  observability: 'observability',
  settings: 'settings',
}

/**
 * 今の画面がどの第 1 階層の下か。プロジェクト（ホームのカードから入る）はホーム。
 * エディタ（`/w/:slug/knowledge/:section/...`）はその種類の画面。
 */
export function currentSection(pathname: string): SectionId | null {
  if (/^\/runs\//.test(pathname)) return 'runs'
  const m = /^\/w\/[^/]+(?:\/([^/]+)(?:\/([^/]+))?)?/.exec(pathname)
  if (!m) return null
  if (!m[1] || m[1] === 'p') return 'home'
  if (m[1] === 'knowledge') return Object.values(KIND_SECTION_ID).find((s) => s === m[2]) ?? null
  return SECTION_SEGMENT[m[1]] ?? null
}
