/** 画面のパス（design 4 章）。組み立てはここだけで行う。 */

import type { SettingsTab } from '@/types/command'

const e = encodeURIComponent

export const paths = {
  home: () => '/app',
  project: (slug: string, fullName: string) => {
    const [owner, repo] = fullName.split('/')
    return `/w/${e(slug)}/p/${e(owner)}/${e(repo)}`
  },
  policies: (slug: string) => `/w/${e(slug)}/policies`,
  settings: (slug: string, tab?: SettingsTab) => `/w/${e(slug)}/settings${tab ? `?tab=${tab}` : ''}`,
  run: (id: string) => `/runs/${e(id)}`,
}
