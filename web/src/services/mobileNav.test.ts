import { describe, expect, it } from 'vitest'
import type { ProjectSummary } from '@/types/project'
import { isCurrentEntry, mobileNavEntries } from './mobileNav'

const projects = [{ id: 'p1', repo_full_name: 'org/shop' }, { id: 'p2', repo_full_name: 'org/api' }] as ProjectSummary[]

describe('mobileNavEntries', () => {
  it('プロジェクトの外では、ホーム・プロジェクト・チーム共通・規約・利用状況・設定', () => {
    const entries = mobileNavEntries({ slug: 'ws', projects, currentRepo: null })
    expect(entries.map((e) => [e.label, e.to])).toEqual([
      ['ワークスペースのホーム', '/w/ws'],
      ['shop', '/w/ws/p/org/shop'],
      ['api', '/w/ws/p/org/api'],
      ['チーム共通', '/w/ws?tab=shared'],
      ['チーム規約', '/w/ws?tab=policies'],
      ['利用状況', '/w/ws?tab=usage'],
      ['設定', '/w/ws/settings'],
    ])
  })
  it('プロジェクトの中では、そのプロジェクトの下にタブを字下げして並べる', () => {
    const entries = mobileNavEntries({ slug: 'ws', projects, currentRepo: 'org/shop' })
    const tabs = entries.filter((e) => e.depth === 1)
    expect(tabs.map((e) => e.label)).toEqual(['仕様', 'スキル', '指示書', '規約', '検査履歴'])
    expect(tabs[0].to).toBe('/w/ws/p/org/shop/specs')
    const shop = entries.findIndex((e) => e.label === 'shop')
    expect(entries[shop + 1].depth).toBe(1)
  })
})

describe('isCurrentEntry', () => {
  it('パスとクエリが一致したときだけ今の画面', () => {
    expect(isCurrentEntry('/w/ws?tab=shared', '/w/ws', '?tab=shared')).toBe(true)
    expect(isCurrentEntry('/w/ws', '/w/ws', '?tab=shared')).toBe(false)
  })
})
