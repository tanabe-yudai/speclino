import { describe, expect, it } from 'vitest'
import { buildCommands } from './commands'
import type { Me } from '@/types/me'

const me: Me = {
  uid: 'u1',
  email: 'owner@example.com',
  email_verified: true,
  workspaces: [{ slug: 'ws', name: 'Example', role: 'owner' }],
}

describe('buildCommands', () => {
  it('ホーム・各ワークスペースのプロジェクトと設定を並べる', () => {
    const commands = buildCommands({
      me,
      repositories: {
        ws: [{ repo_full_name: 'example-org/example-repo', github_repo_id: 1, default_branch: 'main' }],
      },
    })
    expect(commands.map((c) => c.path)).toEqual([
      '/app',
      '/w/ws/p/example-org/example-repo',
      '/w/ws/settings',
    ])
  })
  it('所属が無くても落ちずにホームだけ', () => {
    expect(buildCommands({ me: { ...me, workspaces: [] }, repositories: {} }).map((c) => c.path)).toEqual(['/app'])
    expect(buildCommands({ me: null, repositories: {} }).map((c) => c.path)).toEqual(['/app'])
  })
})
