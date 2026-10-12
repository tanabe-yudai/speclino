import { describe, expect, it } from 'vitest'
import type { ProjectSummary } from '@/types/project'
import { knowledgeCrumbs, policiesCrumbs, runCrumbs } from './breadcrumbs'

const project = { id: 'p1', repo_full_name: 'org/shop' } as ProjectSummary

describe('knowledgeCrumbs', () => {
  it('知識 › 種類 › 名前', () => {
    expect(knowledgeCrumbs({ slug: 'ws', kind: 'spec', name: 'orders', fromProject: null })).toEqual([
      { label: '知識', to: '/w/ws/knowledge' },
      { label: '仕様', to: '/w/ws/knowledge?kind=spec' },
      { label: 'orders' },
    ])
  })
  it('プロジェクトから開いたらプロジェクト › 種類（知識のタブ）', () => {
    expect(knowledgeCrumbs({ slug: 'ws', kind: 'skill', name: null, fromProject: project })).toEqual([
      { label: 'shop', to: '/w/ws/p/org/shop' },
      { label: 'スキル', to: '/w/ws/p/org/shop/knowledge?kind=skill' },
      { label: '新しいスキル' },
    ])
  })
  it('改訂の履歴はエディタを経由する', () => {
    const crumbs = knowledgeCrumbs({ slug: 'ws', kind: 'spec', name: 'orders', fromProject: project, revisionsOf: 'id1' })
    expect(crumbs.slice(2)).toEqual([{ label: 'orders', to: '/w/ws/knowledge/specs/id1?from=org%2Fshop' }, { label: '改訂の履歴' }])
  })
})

describe('policiesCrumbs / runCrumbs', () => {
  it('規約は知識の下、検査の詳細は検査の下', () => {
    expect(policiesCrumbs('ws')).toEqual([{ label: '知識', to: '/w/ws/knowledge' }, { label: '規約' }])
    expect(runCrumbs('ws', 'org/shop #1')).toEqual([{ label: '検査', to: '/w/ws/runs' }, { label: 'org/shop #1' }])
    expect(runCrumbs(null, 'x')).toEqual([{ label: 'x' }])
  })
})
