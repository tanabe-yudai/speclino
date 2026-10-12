import { describe, expect, it } from 'vitest'
import { SECTIONS, currentSection, sectionPath } from './navSections'

describe('navSections', () => {
  it('ホーム・知識・検査・観測・設定のパス', () => {
    expect(SECTIONS.map((s) => sectionPath('ws', s))).toEqual(['/w/ws', '/w/ws/knowledge', '/w/ws/runs', '/w/ws/observability', '/w/ws/settings'])
  })
  it('今の画面の第 1 階層', () => {
    expect(currentSection('/w/ws')).toBe('home')
    expect(currentSection('/w/ws/p/org/shop/knowledge')).toBe('home')
    expect(currentSection('/w/ws/knowledge/specs/id1')).toBe('knowledge')
    expect(currentSection('/w/ws/knowledge/policies')).toBe('knowledge')
    expect(currentSection('/w/ws/runs/r1')).toBe('runs')
    expect(currentSection('/runs/r1')).toBe('runs')
    expect(currentSection('/w/ws/observability')).toBe('observability')
    expect(currentSection('/w/ws/settings/agents')).toBe('settings')
    expect(currentSection('/app')).toBeNull()
  })
})
