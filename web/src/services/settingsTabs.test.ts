import { describe, expect, it } from 'vitest'
import { pickSettingsTab, visibleSettingsTabs } from './settingsTabs'

describe('visibleSettingsTabs', () => {
  it('owner はプロジェクト・リポジトリ・メンバー・リポジトリ同期・エージェント接続の各トークン', () => {
    expect(visibleSettingsTabs('owner')).toEqual(['projects', 'repositories', 'members', 'sync', 'tokens'])
  })
  it('owner 以外は エージェント接続トークンを出さない（owner の操作）。同期トークンの一覧はメンバー全員', () => {
    expect(visibleSettingsTabs('approver')).toEqual(['projects', 'repositories', 'members', 'sync'])
    expect(visibleSettingsTabs('member')).toEqual(['projects', 'repositories', 'members', 'sync'])
    expect(visibleSettingsTabs(null)).toEqual(['projects', 'repositories', 'members'])
  })
})

describe('pickSettingsTab', () => {
  it('見えるタブならその値、見えない・不正なら先頭', () => {
    expect(pickSettingsTab('tokens', ['repositories', 'tokens'])).toBe('tokens')
    expect(pickSettingsTab('tokens', ['repositories'])).toBe('repositories')
    expect(pickSettingsTab('nope', ['repositories', 'tokens'])).toBe('repositories')
    expect(pickSettingsTab(null, ['repositories', 'tokens'])).toBe('repositories')
    expect(pickSettingsTab('members', ['projects', 'repositories', 'members'])).toBe('members')
  })
})
