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
  workspaces: WorkspaceMembership[]
}
