import { describe, expect, it } from 'vitest'
import { kindTab, parseProjectTab, tabKind } from './projectTabs'
import { pickWorkspaceTab } from './workspaceTabs'

describe('parseProjectTab', () => {
  it('タブが無ければ概要', () => {
    expect(parseProjectTab(undefined)).toBe('overview')
  })
  it('知っているタブはそのまま、知らないタブは null', () => {
    expect(parseProjectTab('specs')).toBe('specs')
    expect(parseProjectTab('runs')).toBe('runs')
    expect(parseProjectTab('knowledge')).toBeNull()
    expect(parseProjectTab('')).toBeNull()
  })
  it('種類とタブを相互に引ける', () => {
    expect(tabKind('skills')).toBe('skill')
    expect(tabKind('policies')).toBeNull()
    expect(kindTab('instructions')).toBe('instructions')
    expect(kindTab('spec')).toBe('specs')
  })
})

describe('pickWorkspaceTab', () => {
  it('無い・不正ならプロジェクト', () => {
    expect(pickWorkspaceTab(null)).toBe('projects')
    expect(pickWorkspaceTab('x')).toBe('projects')
  })
  it('知っているタブはそのまま', () => {
    expect(pickWorkspaceTab('shared')).toBe('shared')
    expect(pickWorkspaceTab('usage')).toBe('usage')
  })
})
