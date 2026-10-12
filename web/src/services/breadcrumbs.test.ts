import { describe, expect, it } from 'vitest'
import type { ProjectSummary } from '@/types/project'
import { knowledgeCrumbs, runCrumbs } from './breadcrumbs'

const project = { id: 'p1', repo_full_name: 'org/shop' } as ProjectSummary

describe('knowledgeCrumbs', () => {
  it('種類の画面 › 名前', () => {
    expect(knowledgeCrumbs({ slug: 'ws', kind: 'spec', name: 'orders', fromProject: null })).toEqual([
      { label: '仕様', to: '/w/ws/specs' },
      { label: 'orders' },
    ])
    expect(knowledgeCrumbs({ slug: 'ws', kind: 'instructions', name: null, fromProject: null })).toEqual([
      { label: '指示書', to: '/w/ws/instructions' },
      { label: '新しい指示書' },
    ])
  })
  it('仕様をプロジェクトから開いたらプロジェクト › 仕様のタブ', () => {
    expect(knowledgeCrumbs({ slug: 'ws', kind: 'spec', name: null, fromProject: project })).toEqual([
      { label: 'shop', to: '/w/ws/p/org/shop' },
      { label: '仕様', to: '/w/ws/p/org/shop/specs' },
      { label: '新しい仕様' },
    ])
  })
  it('スキルはプロジェクトから開いても種類の画面（ワークスペース全体）', () => {
    expect(knowledgeCrumbs({ slug: 'ws', kind: 'skill', name: 'fmt', fromProject: project })).toEqual([
      { label: 'スキル', to: '/w/ws/skills' },
      { label: 'fmt' },
    ])
  })
  it('改訂の履歴はエディタを経由する', () => {
    const crumbs = knowledgeCrumbs({ slug: 'ws', kind: 'spec', name: 'orders', fromProject: project, revisionsOf: 'id1' })
    expect(crumbs.slice(2)).toEqual([{ label: 'orders', to: '/w/ws/knowledge/specs/id1?from=org%2Fshop' }, { label: '改訂の履歴' }])
  })
})

describe('runCrumbs', () => {
  it('検査の詳細は検査の下', () => {
    expect(runCrumbs('ws', 'org/shop #1')).toEqual([{ label: '検査', to: '/w/ws/runs' }, { label: 'org/shop #1' }])
    expect(runCrumbs(null, 'x')).toEqual([{ label: 'x' }])
  })
})
