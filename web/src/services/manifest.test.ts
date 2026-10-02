import { describe, expect, it } from 'vitest'
import { describeTeamPolicy } from './manifest'
import type { RunManifest } from '@/types/run'

const base = { policy_team: null, policy_revision: null } as unknown as RunManifest

describe('describeTeamPolicy', () => {
  it('適用したチーム規約の改訂を出す（どの改訂で判定したかを残す）', () => {
    expect(describeTeamPolicy({ ...base, policy_team: 'example-org', policy_revision: 2 })).toBe(
      'チーム規約: example-org 改訂 2',
    )
  })
  it('チーム規約が無い run はそう書く', () => {
    expect(describeTeamPolicy(base)).toBe('チーム規約: 適用なし')
  })
})
