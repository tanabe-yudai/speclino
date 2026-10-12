import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { actorLabel } from '@/services/actors'
import type { WorkspaceMember } from '@/types/member'
import { AuditLog } from './AuditLog'
import { RevisionHistory } from './RevisionHistory'

const members: WorkspaceMember[] = [
  {
    uid: 'AuthorUid1234567',
    display_name: '田辺',
    email: 't@example.com',
    email_verified: true,
    label: '田辺（t@example.com）',
    role: 'owner',
    joined_at: '2026-10-01T00:00:00Z',
  },
]
const label = (uid: string | null | undefined) => actorLabel(uid, members)

describe('改訂の履歴と監査ログの人の表示', () => {
  it('作者・承認者・操作した人を表示名で出し、uid をそのまま出さない', () => {
    const history = renderToStaticMarkup(
      <RevisionHistory
        actorLabel={label}
        items={[
          {
            revision: 2,
            status: 'active',
            author: 'AuthorUid1234567',
            approver: 'LeftMemberUid999',
            note: '',
            rule_count: 1,
            created_at: '2026-10-01T00:00:00Z',
            approved_at: '2026-10-02T00:00:00Z',
          },
        ]}
      />,
    )
    expect(history).toContain('作成 田辺（t@example.com）')
    expect(history).toContain('承認 LeftMemb…')
    expect(history).not.toContain('AuthorUid1234567')

    const audit = renderToStaticMarkup(
      <AuditLog
        actorLabel={label}
        items={[{ ts: '2026-10-02T00:00:00Z', actor: 'AuthorUid1234567', action: 'policy.approve', target: 'ws@2', decision: 'approved', detail: {} }]}
      />,
    )
    expect(audit).toContain('田辺')
    expect(audit).not.toContain('AuthorUid1234567')
  })
})
