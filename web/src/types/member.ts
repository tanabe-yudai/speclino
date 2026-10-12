/** メンバーと招待の API の型（S56）。src/speclino/api/routes/members.py に対応させる。 */

import type { WorkspaceRole } from './me'

export interface WorkspaceMember {
  uid: string
  /** 本人が設定した表示名。未設定は null。 */
  display_name: string | null
  /** 最後のサインインのメール（無ければ招待の受諾で入ったメール）。どちらも無ければ null。 */
  email: string | null
  email_verified: boolean
  /** API が決めた表示（表示名 → メール → uid の短縮）。 */
  label: string
  role: WorkspaceRole
  joined_at: string
}

export interface Invitation {
  id: string
  email: string
  role: WorkspaceRole
  created_by: string
  created_at: string
  expires_at: string
  accepted_at: string | null
  revoked_at: string | null
}

/** 発行直後の応答だけに含まれる（平文はこの 1 回しか見えない）。 */
export interface IssuedInvitation extends Invitation {
  token: string
}

export interface InvitationPreview {
  slug: string
  name: string | null
  email: string
  role: WorkspaceRole
  expires_at: string
}

export interface AcceptedInvitation {
  slug: string
  role: WorkspaceRole
  status: 'joined' | 'already_member'
}

/** 一覧での招待の状態（使用済み・取り消し済みは API が返さない）。 */
export type InvitationStatus = 'pending' | 'expired'

/** uid → 画面に出す名前（`useActorLabel` が返す）。 */
export type ActorLabelFn = (uid: string | null | undefined) => string
