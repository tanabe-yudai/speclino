import { describe, expect, it } from 'vitest'
import type { FetchHealth, WorkspaceHealth } from '@/types/observability'
import { staleRepositories } from './home'

function fetch(repo: string | null, behind: number | null, extra: Partial<FetchHealth> = {}): FetchHealth {
  return {
    channel: 'sync',
    token_id: 't',
    token_name: 'n',
    revoked: false,
    uid: null,
    member_email: null,
    member_label: null,
    project_id: null,
    repo_full_name: repo,
    last_fetched_at: null,
    last_generation: null,
    behind_by: behind,
    ...extra,
  }
}

describe('staleRepositories', () => {
  it('sync で遅れているリポジトリ（いちばん遅れたもの）。失効・MCP・遅れなしは除く', () => {
    const health = {
      fetches: [
        fetch('org/shop', 1),
        fetch('org/shop', 3),
        fetch('org/api', 0),
        fetch('org/old', 2, { revoked: true }),
        fetch('org/mcp', 2, { channel: 'mcp' }),
      ],
    } as WorkspaceHealth
    expect(staleRepositories(health)).toEqual([{ repo: 'org/shop', behindBy: 3 }])
  })
})
