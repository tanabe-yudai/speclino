import { describe, expect, it } from 'vitest'
import { actorLabel } from './actors'
import type { WorkspaceMember } from '@/types/member'

const members: WorkspaceMember[] = [
  { uid: 'u1', email: 'a@example.com', role: 'owner', joined_at: '2026-10-01T00:00:00Z' },
  { uid: 'u2', email: null, role: 'member', joined_at: '2026-10-01T00:00:00Z' },
]

describe('actorLabel', () => {
  it('メールがあればメールを出す', () => {
    expect(actorLabel('u1', members)).toBe('a@example.com')
  })
  it('メールが無い・一覧に無い・未取得・空の uid は「メンバー」で、uid を出さない', () => {
    expect(actorLabel('u2', members)).toBe('メンバー')
    expect(actorLabel('judge', members)).toBe('メンバー')
    expect(actorLabel('u1', null)).toBe('メンバー')
    expect(actorLabel(null, members)).toBe('メンバー')
  })
})
