/** メンバーと招待の表示ロジック（S56）。コンポーネントには埋め込まない。 */

import { personLabel } from '@/services/actors'
import type { Me, WorkspaceRole } from '@/types/me'
import type { Invitation, InvitationPreview, InvitationStatus, WorkspaceMember } from '@/types/member'

const TOKEN_PREFIX = 'slinv_'
// API のトークンは `slinv_` + URL セーフな base64
const TOKEN_PATTERN = /^slinv_[A-Za-z0-9_-]+$/
// API（src/speclino/api/routes/members.py）と同じ形だけの確認
const EMAIL_PATTERN = /^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$/

const ROLE_ORDER: Record<WorkspaceRole, number> = { owner: 0, approver: 1, member: 2 }

export const INVITATION_STATUS_LABELS: Record<InvitationStatus, string> = {
  pending: '受諾待ち',
  expired: '期限切れ',
}

/** 招待リンク。トークンはフラグメント（`#`）に載せ、サーバーのアクセスログに残さない。 */
export function invitationLink(origin: string, token: string): string {
  return `${origin}/invite#${token}`
}

/** `location.hash` から招待のトークンを取り出す。形が違えば null。 */
export function tokenFromHash(hash: string): string | null {
  const token = hash.replace(/^#/, '')
  return token.length > TOKEN_PREFIX.length && TOKEN_PATTERN.test(token) ? token : null
}

export function invitationStatus(invitation: Invitation, now: Date): InvitationStatus {
  return new Date(invitation.expires_at).getTime() > now.getTime() ? 'pending' : 'expired'
}

/** その人が最後の owner か（最後の owner は降格・削除できない。API も 409 で拒否する）。 */
export function isLastOwner(target: WorkspaceMember, members: WorkspaceMember[]): boolean {
  return target.role === 'owner' && members.filter((m) => m.role === 'owner').length <= 1
}

export function memberLabel(member: WorkspaceMember): string {
  return personLabel(member.uid, member.display_name, member.email)
}

/** 一覧の 2 行目に出すメール（表示名を出しているときだけ。表示名が無ければ 1 行目がメール）。 */
export function memberSubLabel(member: WorkspaceMember): string | null {
  return member.display_name ? member.email : null
}

export function sortMembers(members: WorkspaceMember[]): WorkspaceMember[] {
  return [...members].sort(
    (a, b) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role] || memberLabel(a).localeCompare(memberLabel(b)),
  )
}

/** 招待・role の変更・削除は owner だけ。 */
export function canManageMembers(role: WorkspaceRole | null): boolean {
  return role === 'owner'
}

export function isValidEmail(email: string): boolean {
  return EMAIL_PATTERN.test(email)
}

/**
 * 受諾できない理由（API と同じ規則: 確認済みのメールが招待の宛先と一致すること）。受諾できるなら null。
 * 画面で先に示すだけで、判断は API が行う。
 */
export function acceptBlocker(me: Pick<Me, 'email' | 'email_verified'>, preview: InvitationPreview): string | null {
  if (!me.email || !me.email_verified) {
    return 'ログイン中のアカウントのメールアドレスが確認されていないため、受諾できません。'
  }
  if (me.email.trim().toLowerCase() !== preview.email) {
    return `この招待は ${preview.email} 宛てです。そのメールアドレスのアカウントでログインし直してください。`
  }
  return null
}
