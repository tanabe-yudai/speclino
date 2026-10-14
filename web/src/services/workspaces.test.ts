import { describe, expect, it } from 'vitest'
import { landingPath, pickWorkspace, workspaceSlugError } from './workspaces'
import type { WorkspaceMembership } from '@/types/me'

const ws: WorkspaceMembership[] = [
  { slug: 'a', name: 'A', role: 'owner' },
  { slug: 'b', name: 'B', role: 'member' },
]

describe('pickWorkspace', () => {
  it('URL の slug があればそれだけ', () => {
    expect(pickWorkspace(ws, 'b')?.slug).toBe('b')
  })
  it('所属していない slug は null（先頭に化けさせない）', () => {
    expect(pickWorkspace(ws, 'zzz')).toBeNull()
  })
  it('slug が無ければ所属の先頭、所属が無ければ null', () => {
    expect(pickWorkspace(ws, undefined)?.slug).toBe('a')
    expect(pickWorkspace([], undefined)).toBeNull()
  })
})

describe('landingPath', () => {
  it('所属が 1 つならそのホームへ直接入る', () => {
    expect(landingPath([ws[0]])).toBe('/w/a')
  })
  it('所属が 0 か 2 つ以上なら自分のワークスペースの画面で選ぶ', () => {
    expect(landingPath([])).toBe('/workspaces')
    expect(landingPath(ws)).toBe('/workspaces')
  })
})

describe('workspaceSlugError', () => {
  it.each(['ab', 'judge-sandbox', 'a1', 'x'.repeat(40)])('%s は通す', (slug) => {
    expect(workspaceSlugError(slug)).toBeNull()
  })
  it.each(['', 'a', 'Upper', '-lead', 'trail-', 'two--hyphens', 'with_underscore', 'x'.repeat(41), '日本'])(
    '%s は誤り',
    (slug) => {
      expect(workspaceSlugError(slug)).not.toBeNull()
    },
  )
})
