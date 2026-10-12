import { describe, expect, it } from 'vitest'
import {
  acceptBlocker,
  canManageMembers,
  invitationLink,
  invitationStatus,
  isLastOwner,
  isValidEmail,
  memberLabel,
  memberSubLabel,
  sortMembers,
  tokenFromHash,
} from './members'
import type { Invitation, WorkspaceMember } from '@/types/member'

const member = (
  uid: string,
  role: WorkspaceMember['role'],
  email: string | null = null,
  display_name: string | null = null,
): WorkspaceMember => ({
  uid,
  display_name,
  email,
  email_verified: email !== null,
  label: '',
  role,
  joined_at: '2026-10-01T00:00:00Z',
})

const invitation = (expires_at: string): Invitation => ({
  id: 'i1',
  email: 'owner@example.com',
  role: 'member',
  created_by: 'u1',
  created_at: '2026-10-01T00:00:00Z',
  expires_at,
  accepted_at: null,
  revoked_at: null,
})

describe('invitationLink / tokenFromHash', () => {
  it('トークンはフラグメントに載せる（サーバーのログに残さない）', () => {
    expect(invitationLink('https://speclino.example', 'slinv_abc-_1')).toBe('https://speclino.example/invite#slinv_abc-_1')
  })
  it('フラグメントから招待のトークンだけを取り出す', () => {
    expect(tokenFromHash('#slinv_abc-_1')).toBe('slinv_abc-_1')
    expect(tokenFromHash('')).toBeNull()
    expect(tokenFromHash('#other')).toBeNull()
    expect(tokenFromHash('#slinv_')).toBeNull()
    expect(tokenFromHash('#slinv_a b')).toBeNull()
  })
})

describe('invitationStatus', () => {
  const now = new Date('2026-10-04T00:00:00Z')
  it('期限前は有効、期限を過ぎたら期限切れ', () => {
    expect(invitationStatus(invitation('2026-10-05T00:00:00Z'), now)).toBe('pending')
    expect(invitationStatus(invitation('2026-10-03T23:59:59Z'), now)).toBe('expired')
  })
})

describe('isLastOwner', () => {
  it('owner が 1 人なら、その人は降格・削除できない', () => {
    const members = [member('a', 'owner'), member('b', 'member')]
    expect(isLastOwner(members[0], members)).toBe(true)
    expect(isLastOwner(members[1], members)).toBe(false)
  })
  it('owner が 2 人なら外せる', () => {
    const members = [member('a', 'owner'), member('b', 'owner')]
    expect(isLastOwner(members[0], members)).toBe(false)
  })
})

describe('sortMembers / memberLabel', () => {
  it('role の重い順、同じ role はメール（無ければ uid）順', () => {
    const sorted = sortMembers([
      member('z', 'member', 'b@example.com'),
      member('y', 'owner'),
      member('x', 'approver'),
      member('w', 'member', 'a@example.com'),
    ])
    expect(sorted.map((m) => m.uid)).toEqual(['y', 'x', 'w', 'z'])
  })
  it('表示名 → メール → uid の短縮の順に出す', () => {
    expect(memberLabel(member('uid-1', 'member'))).toBe('uid-1')
    expect(memberLabel(member('Xy12abcdEFGHijkl', 'member'))).toBe('Xy12abcd…')
    expect(memberLabel(member('uid-1', 'member', 'owner@example.com'))).toBe('owner@example.com')
    expect(memberLabel(member('uid-1', 'member', 'owner@example.com', '田辺'))).toBe('田辺')
  })
  it('表示名を出しているときだけ 2 行目にメールを出す', () => {
    expect(memberSubLabel(member('uid-1', 'member', 'owner@example.com', '田辺'))).toBe('owner@example.com')
    expect(memberSubLabel(member('uid-1', 'member', 'owner@example.com'))).toBeNull()
  })
})

describe('canManageMembers', () => {
  it('owner だけ', () => {
    expect(canManageMembers('owner')).toBe(true)
    expect(canManageMembers('approver')).toBe(false)
    expect(canManageMembers('member')).toBe(false)
    expect(canManageMembers(null)).toBe(false)
  })
})

describe('isValidEmail', () => {
  it('形だけを確かめる（API と同じ規則）', () => {
    expect(isValidEmail('owner@example.com')).toBe(true)
    expect(isValidEmail(' owner@example.com ')).toBe(true)
    expect(isValidEmail('owner@example')).toBe(false)
    expect(isValidEmail('not-an-email')).toBe(false)
    expect(isValidEmail('')).toBe(false)
  })
})

describe('acceptBlocker', () => {
  const preview = { slug: 'example-org', name: null, email: 'owner@example.com', role: 'member' as const, expires_at: '' }
  it('確認済みで同じメール（大文字小文字は問わない）なら受諾できる', () => {
    expect(acceptBlocker({ email: 'Owner@Example.com', email_verified: true }, preview)).toBeNull()
  })
  it('メールが違う・未確認・無いときは理由を返す（API も拒否する）', () => {
    expect(acceptBlocker({ email: 'other@example.com', email_verified: true }, preview)).toContain('owner@example.com')
    expect(acceptBlocker({ email: 'owner@example.com', email_verified: false }, preview)).toContain('確認')
    expect(acceptBlocker({ email: null, email_verified: false }, preview)).not.toBeNull()
  })
})
