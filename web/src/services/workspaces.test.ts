import { describe, expect, it } from 'vitest'
import { pickWorkspace } from './workspaces'
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
