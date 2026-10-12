import { describe, expect, it } from 'vitest'
import { parseProjectTab } from './projectTabs'

describe('parseProjectTab', () => {
  it('タブが無ければ概要', () => {
    expect(parseProjectTab(undefined)).toBe('overview')
  })
  it('概要・知識・検査はそのまま、旧いタブと知らないタブは null', () => {
    expect(parseProjectTab('knowledge')).toBe('knowledge')
    expect(parseProjectTab('runs')).toBe('runs')
    expect(parseProjectTab('specs')).toBeNull()
    expect(parseProjectTab('')).toBeNull()
  })
})
