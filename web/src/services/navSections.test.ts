import { describe, expect, it } from 'vitest'
import { BOTTOM_TABS, MORE_SECTIONS, SECTIONS, currentSection, sectionPath } from './navSections'

describe('navSections', () => {
  it('PC の上部: ホーム・仕様・スキル・指示書・規約・検査・観測・設定のパス', () => {
    expect(SECTIONS.map((s) => sectionPath('ws', s))).toEqual([
      '/w/ws',
      '/w/ws/specs',
      '/w/ws/skills',
      '/w/ws/instructions',
      '/w/ws/policies',
      '/w/ws/runs',
      '/w/ws/observability',
      '/w/ws/settings',
    ])
  })
  it('スマホの下部: ホーム・仕様・検査・観測、その他にスキル・指示書・規約・設定', () => {
    expect(BOTTOM_TABS).toEqual(['home', 'specs', 'runs', 'observability'])
    expect(MORE_SECTIONS).toEqual(['skills', 'instructions', 'policies', 'settings'])
  })
  it('今の画面の第 1 階層', () => {
    expect(currentSection('/w/ws')).toBe('home')
    expect(currentSection('/w/ws/p/org/shop/specs')).toBe('home')
    expect(currentSection('/w/ws/specs')).toBe('specs')
    expect(currentSection('/w/ws/skills')).toBe('skills')
    expect(currentSection('/w/ws/policies')).toBe('policies')
    expect(currentSection('/w/ws/knowledge/specs/id1')).toBe('specs')
    expect(currentSection('/w/ws/knowledge/instructions/new')).toBe('instructions')
    expect(currentSection('/w/ws/knowledge/x/id1')).toBeNull()
    expect(currentSection('/w/ws/runs/r1')).toBe('runs')
    expect(currentSection('/runs/r1')).toBe('runs')
    expect(currentSection('/w/ws/observability')).toBe('observability')
    expect(currentSection('/w/ws/settings/retired')).toBe('settings')
    expect(currentSection('/app')).toBeNull()
  })
})
