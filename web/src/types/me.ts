/** `GET /api/me` の応答（S53）。 */

export type WorkspaceRole = 'owner' | 'approver' | 'member'

export interface WorkspaceMembership {
  slug: string
  name: string | null
  role: WorkspaceRole
}

export interface Me {
  uid: string
  email: string | null
  email_verified: boolean
  /** 本人が設定した表示名（初回だけ ID トークンの名前）。未設定は null。 */
  display_name: string | null
  workspaces: WorkspaceMembership[]
}

/** `PATCH /api/me/profile` の応答。 */
export interface MyProfile {
  uid: string
  display_name: string | null
  email: string | null
  email_verified: boolean
}
