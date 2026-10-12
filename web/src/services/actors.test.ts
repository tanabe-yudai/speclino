import { describe, expect, it } from 'vitest'
import { actorLabel, personLabel, shortUid } from './actors'
import type { WorkspaceMember } from '@/types/member'

const member = (uid: string, display_name: string | null, email: string | null): WorkspaceMember => ({
  uid,
  display_name,
  email,
  email_verified: email !== null,
  label: '',
  role: 'member',
  joined_at: '2026-10-01T00:00:00Z',
})

const members: WorkspaceMember[] = [
  member('u1', null, 'a@example.com'),
  member('u2', null, null),
  member('u3', '田辺', 't@example.com'),
]

describe('actorLabel', () => {
  it('表示名 → メール → uid の短縮の順に出す', () => {
    expect(actorLabel('u3', members)).toBe('田辺')
    expect(actorLabel('u1', members)).toBe('a@example.com')
    expect(actorLabel('u2', members)).toBe('u2')
  })
  it('一覧に無い・未取得の uid は短縮した uid、GitHub の actor はそのまま、空は「メンバー」', () => {
    expect(actorLabel('Xy12abcdEFGHijkl', members)).toBe('Xy12abcd…')
    expect(actorLabel('u1', null)).toBe('u1')
    expect(actorLabel('github:12345', members)).toBe('github:12345')
    expect(actorLabel(null, members)).toBe('メンバー')
  })
})

describe('personLabel / shortUid', () => {
  it('空の表示名はメールに、メールも無ければ短縮した uid に落ちる', () => {
    expect(personLabel('Xy12abcdEFGHijkl', '', null)).toBe('Xy12abcd…')
    expect(shortUid('abcdefghi')).toBe('abcdefghi')
  })
})
