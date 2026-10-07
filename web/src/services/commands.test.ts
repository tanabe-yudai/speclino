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
      projects: {
        ws: [
          {
            id: 'p1',
            repo_full_name: 'example-org/example-repo',
            default_branch: 'main',
            status: 'not_checked',
            latest_run: null,
            counts: null,
            unanalyzed: null,
            status_counts: null,
            state: null,
          },
        ],
      },
    })
    expect(commands.map((c) => c.path)).toEqual([
      '/app',
      '/w/ws',
      '/w/ws/p/example-org/example-repo',
      '/w/ws?tab=shared',
      '/w/ws?tab=policies',
      '/w/ws?tab=usage',
      '/w/ws/settings',
    ])
  })
  it('所属が無くても落ちずにホームだけ', () => {
    expect(buildCommands({ me: { ...me, workspaces: [] }, projects: {} }).map((c) => c.path)).toEqual(['/app'])
    expect(buildCommands({ me: null, projects: {} }).map((c) => c.path)).toEqual(['/app'])
  })
  it('ワークスペースごとにホームへの候補がある', () => {
    const cmds = buildCommands({ me, projects: {} })
    expect(cmds.find((c) => c.id === 'workspace:ws')).toMatchObject({
      label: 'Example のホーム',
      path: '/w/ws',
      group: 'ワークスペース',
    })
  })
  it('ワークスペースごとに規約への候補がある', () => {
    const cmds = buildCommands({ me, projects: {} })
    expect(cmds.find((c) => c.id === 'policies:ws')).toMatchObject({ label: 'Example のチーム規約', path: '/w/ws?tab=policies' })
  })
})
