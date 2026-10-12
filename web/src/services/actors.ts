/**
 * uid を人が読む表示にする（API の services/profiles.py の profile_label と同じ規則）。
 * 表示名は本人が自由に決められるので、作者・承認者・監査では表示名に必ずメール（無ければ uid の短縮）を
 * 添え、同名の別人を見分けられるようにする。
 */

import type { WorkspaceMember } from '@/types/member'

export const UNKNOWN_ACTOR_LABEL = 'メンバー'
const GITHUB_ACTOR_PREFIX = 'github:'
const SHORT_UID_LENGTH = 8

export function shortUid(uid: string): string {
  return uid.length <= SHORT_UID_LENGTH + 1 ? uid : `${uid.slice(0, SHORT_UID_LENGTH)}…`
}

/** メンバーの一覧の 1 行目（2 行目にメールと確認の印を出すので、ここでは添えない）。 */
export function personLabel(uid: string, displayName: string | null, email: string | null): string {
  return displayName || email || shortUid(uid)
}

/** 表示名（メール、無ければ uid の短縮）→ メール → uid の短縮。未確認のメールには印を添える。 */
export function identityLabel(uid: string, displayName: string | null, email: string | null, emailVerified: boolean): string {
  if (displayName) {
    if (!email) return `${displayName}（${shortUid(uid)}）`
    return `${displayName}（${email}${emailVerified ? '' : '・未確認'}）`
  }
  if (email) return emailVerified ? email : `${email}（未確認）`
  return shortUid(uid)
}

/**
 * uid → 表示。メンバーの一覧にあれば identityLabel、無ければ（外したメンバー・未取得）uid の短縮。
 * GitHub の actor（`github:<id>`）はそのまま。uid が無ければ「メンバー」。
 */
export function actorLabel(uid: string | null | undefined, members: WorkspaceMember[] | null): string {
  if (!uid) return UNKNOWN_ACTOR_LABEL
  if (uid.startsWith(GITHUB_ACTOR_PREFIX)) return uid
  const member = members?.find((m) => m.uid === uid)
  return member ? identityLabel(uid, member.display_name, member.email, member.email_verified) : shortUid(uid)
}
