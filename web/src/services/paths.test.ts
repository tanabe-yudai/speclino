import { describe, expect, it } from 'vitest'
import { paths } from './paths'

describe('paths', () => {
  it('プロジェクトは owner/repo をパスの 2 段にする', () => {
    expect(paths.project('ws', 'example-org/example-repo')).toBe('/w/ws/p/example-org/example-repo')
  })
  it('設定のタブはクエリで指定する', () => {
    expect(paths.settings('ws')).toBe('/w/ws/settings')
    expect(paths.settings('ws', 'tokens')).toBe('/w/ws/settings?tab=tokens')
  })
  it('slug と名前はエンコードする', () => {
    expect(paths.policies('a b')).toBe('/w/a%20b/policies')
  })
})
