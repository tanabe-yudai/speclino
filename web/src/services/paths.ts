/** 画面のパス（design 4 章）。組み立てはここだけで行う。 */

import type { SettingsTab } from '@/types/command'
import type { ArtifactKind } from '@/types/knowledge'
import { KIND_SECTION } from './knowledge'

const e = encodeURIComponent
const kn = (slug: string, kind: ArtifactKind) => `/w/${e(slug)}/knowledge/${KIND_SECTION[kind]}`

export const paths = {
  home: () => '/app',
  workspace: (slug: string) => `/w/${e(slug)}`,
  project: (slug: string, fullName: string) => {
    const [owner, repo] = fullName.split('/')
    return `/w/${e(slug)}/p/${e(owner)}/${e(repo)}`
  },
  policies: (slug: string) => `/w/${e(slug)}/policies`,
  /** 知識のどの画面でも上部バーの「知識」を選ばれた状態にするための根 */
  knowledgeRoot: (slug: string) => `/w/${e(slug)}/knowledge`,
  knowledge: (slug: string, kind: ArtifactKind = 'spec') => kn(slug, kind),
  knowledgeNew: (slug: string, kind: ArtifactKind) => `${kn(slug, kind)}/new`,
  knowledgeItem: (slug: string, kind: ArtifactKind, id: string) => `${kn(slug, kind)}/${e(id)}`,
  knowledgeRevisions: (slug: string, kind: ArtifactKind, id: string) => `${kn(slug, kind)}/${e(id)}/revisions`,
  settings: (slug: string, tab?: SettingsTab) => `/w/${e(slug)}/settings${tab ? `?tab=${tab}` : ''}`,
  run: (id: string) => `/runs/${e(id)}`,
}
