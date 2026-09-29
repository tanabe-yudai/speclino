import { describe, expect, it } from 'vitest'
import { pickSettingsTab, visibleSettingsTabs } from './settingsTabs'

describe('visibleSettingsTabs', () => {
  it('owner はリポジトリと MCP トークン', () => {
    expect(visibleSettingsTabs('owner')).toEqual(['repositories', 'tokens'])
  })
  it('owner 以外・非メンバーはリポジトリだけ（MCP トークンは owner の操作）', () => {
    expect(visibleSettingsTabs('approver')).toEqual(['repositories'])
    expect(visibleSettingsTabs('member')).toEqual(['repositories'])
    expect(visibleSettingsTabs(null)).toEqual(['repositories'])
  })
})

describe('pickSettingsTab', () => {
  it('見えるタブならその値、見えない・不正なら先頭', () => {
    expect(pickSettingsTab('tokens', ['repositories', 'tokens'])).toBe('tokens')
    expect(pickSettingsTab('tokens', ['repositories'])).toBe('repositories')
    expect(pickSettingsTab('nope', ['repositories', 'tokens'])).toBe('repositories')
    expect(pickSettingsTab(null, ['repositories', 'tokens'])).toBe('repositories')
  })
})
