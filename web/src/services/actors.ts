/** uid を人が読む表示にする。表示名 → メール → uid の短縮（API の services/profiles.py と同じ規則）。 */

import type { WorkspaceMember } from '@/types/member'

export const UNKNOWN_ACTOR_LABEL = 'メンバー'
const GITHUB_ACTOR_PREFIX = 'github:'
const SHORT_UID_LENGTH = 8

export function shortUid(uid: string): string {
  return uid.length <= SHORT_UID_LENGTH + 1 ? uid : `${uid.slice(0, SHORT_UID_LENGTH)}…`
}

export function personLabel(uid: string, displayName: string | null, email: string | null): string {
  return displayName || email || shortUid(uid)
}

/**
 * uid → 表示。メンバーの一覧にあれば表示名かメール、無ければ（外したメンバー・未取得）uid の短縮。
 * GitHub の actor（`github:<id>`）はそのまま。uid が無ければ「メンバー」。
 */
export function actorLabel(uid: string | null | undefined, members: WorkspaceMember[] | null): string {
  if (!uid) return UNKNOWN_ACTOR_LABEL
  if (uid.startsWith(GITHUB_ACTOR_PREFIX)) return uid
  const member = members?.find((m) => m.uid === uid)
  return member ? personLabel(uid, member.display_name, member.email) : shortUid(uid)
}
