/** メンバーと招待のエンドポイント関数（S56）。VITE_MOCK=1 のときはインメモリの見本を返す。 */

import { apiFetch } from './client'
import { ApiError } from './errors'
import type { WorkspaceRole } from '../types/me'
import type {
  AcceptedInvitation,
  Invitation,
  InvitationPreview,
  IssuedInvitation,
  WorkspaceMember,
} from '../types/member'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'
const JSON_HEADERS = { 'Content-Type': 'application/json' }

// VITE_MOCK 用のインメモリ状態（画面確認だけが目的で、リロードすると消える）。
let mockMembers: WorkspaceMember[] = [
  {
    uid: 'mock-user',
    display_name: 'オーナー',
    email: 'owner@example.com',
    email_verified: true,
    label: 'オーナー（owner@example.com）',
    role: 'owner',
    joined_at: '2026-09-20T09:00:00Z',
  },
  {
    uid: 'mock-member',
    display_name: null,
    email: null,
    email_verified: false,
    label: 'mock-mem…',
    role: 'member',
    joined_at: '2026-09-25T09:00:00Z',
  },
]
let mockInvitations: Invitation[] = []

const base = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}`

export async function listMembers(slug: string): Promise<WorkspaceMember[]> {
  if (USE_MOCK) return mockMembers
  return apiFetch<WorkspaceMember[]>(`${base(slug)}/members`)
}

export async function changeMemberRole(slug: string, uid: string, role: WorkspaceRole): Promise<WorkspaceMember> {
  if (USE_MOCK) {
    mockMembers = mockMembers.map((m) => (m.uid === uid ? { ...m, role } : m))
    const changed = mockMembers.find((m) => m.uid === uid)
    if (!changed) throw new ApiError(404, 'メンバーが無い')
    return changed
  }
  return apiFetch<WorkspaceMember>(`${base(slug)}/members/${encodeURIComponent(uid)}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ role }),
  })
}

export async function removeMember(slug: string, uid: string): Promise<void> {
  if (USE_MOCK) {
    mockMembers = mockMembers.filter((m) => m.uid !== uid)
    return
  }
  await apiFetch<void>(`${base(slug)}/members/${encodeURIComponent(uid)}`, { method: 'DELETE' })
}

export async function listInvitations(slug: string): Promise<Invitation[]> {
  if (USE_MOCK) return mockInvitations
  return apiFetch<Invitation[]>(`${base(slug)}/invitations`)
}

export async function issueInvitation(slug: string, email: string, role: WorkspaceRole): Promise<IssuedInvitation> {
  if (USE_MOCK) {
    const now = new Date()
    const issued: IssuedInvitation = {
      id: `mock-inv-${mockInvitations.length + 1}`,
      email: email.trim().toLowerCase(),
      role,
      created_by: 'mock-user',
      created_at: now.toISOString(),
      expires_at: new Date(now.getTime() + 7 * 24 * 3600 * 1000).toISOString(),
      accepted_at: null,
      revoked_at: null,
      token: `slinv_mock${Math.random().toString(36).slice(2)}`,
    }
    mockInvitations = [issued, ...mockInvitations.filter((i) => i.email !== issued.email)]
    return issued
  }
  return apiFetch<IssuedInvitation>(`${base(slug)}/invitations`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email, role }),
  })
}

export async function revokeInvitation(slug: string, id: string): Promise<void> {
  if (USE_MOCK) {
    mockInvitations = mockInvitations.filter((i) => i.id !== id)
    return
  }
  await apiFetch<void>(`${base(slug)}/invitations/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** トークンは URL に載せず、POST の本文で送る。 */
export async function previewInvitation(token: string): Promise<InvitationPreview> {
  if (USE_MOCK) {
    return {
      slug: 'example-org',
      name: 'Example Org',
      email: 'owner@example.com',
      role: 'member',
      expires_at: new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString(),
    }
  }
  return apiFetch<InvitationPreview>('/api/invitations/preview', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ token }),
  })
}

export async function acceptInvitation(token: string): Promise<AcceptedInvitation> {
  if (USE_MOCK) return { slug: 'example-org', role: 'member', status: 'joined' }
  return apiFetch<AcceptedInvitation>('/api/invitations/accept', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ token }),
  })
}
