import { describe, expect, it } from 'vitest'
import { parseProjectTab } from './projectTabs'

describe('parseProjectTab', () => {
  it('タブが無ければ概要', () => {
    expect(parseProjectTab(undefined)).toBe('overview')
  })
  it('概要・仕様・検査はそのまま、旧いタブと知らないタブは null', () => {
    expect(parseProjectTab('specs')).toBe('specs')
    expect(parseProjectTab('runs')).toBe('runs')
    expect(parseProjectTab('knowledge')).toBeNull()
    expect(parseProjectTab('')).toBeNull()
  })
})
