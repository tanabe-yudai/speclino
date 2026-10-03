/** メンバーと招待の API の型（S56）。src/speclino/api/routes/members.py に対応させる。 */

import type { WorkspaceRole } from './me'

export interface WorkspaceMember {
  uid: string
  /** 招待の受諾で入った確認済みのメール。管理者が直接追加したメンバーは null。 */
  email: string | null
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
