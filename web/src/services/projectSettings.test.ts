import { describe, expect, it } from 'vitest'
import { draftSettings, onboardingDone, parseApprovers, settingsEqual } from './projectSettings'
import type { OnboardingStep, ProjectSettings, ProjectSettingsView } from '@/types/projectSettings'

const base: ProjectSettings = {
  fail_on: ['VIOLATED', 'UNKNOWN', 'REVIEW'],
  agent_enabled: true,
  test_run_enabled: true,
  approvers: [],
}

describe('parseApprovers', () => {
  it('カンマ・空白・改行で区切り、空は捨てる', () => {
    expect(parseApprovers(' octo-cat, a1\nb2  ')).toEqual({ approvers: ['octo-cat', 'a1', 'b2'], error: null })
    expect(parseApprovers('')).toEqual({ approvers: [], error: null })
  })
  it('GitHub のログイン名でないものは理由つきで拒む', () => {
    expect(parseApprovers('ok, -bad').error).toContain('-bad')
    expect(parseApprovers('dependabot[bot]').error).not.toBeNull()
    expect(parseApprovers('a'.repeat(40)).error).not.toBeNull()
  })
  it('重複は拒む', () => {
    expect(parseApprovers('octo, octo').error).toContain('重複')
  })
})

describe('settingsEqual', () => {
  it('承認者の並びも含めて比べる', () => {
    expect(settingsEqual(base, { ...base })).toBe(true)
    expect(settingsEqual(base, { ...base, agent_enabled: false })).toBe(false)
    expect(settingsEqual({ ...base, approvers: ['a', 'b'] }, { ...base, approvers: ['b', 'a'] })).toBe(false)
  })
})

describe('onboardingDone', () => {
  const step = (state: OnboardingStep['state']): OnboardingStep => ({ id: 'app', state, detail: '' })
  it('すべて done のときだけ完了（未確認は完了にしない）', () => {
    expect(onboardingDone([step('done'), step('done')])).toBe(true)
    expect(onboardingDone([step('done'), step('unknown')])).toBe(false)
    expect(onboardingDone([step('todo')])).toBe(false)
    expect(onboardingDone([])).toBe(false)
  })
})

describe('draftSettings', () => {
  const view = (canEditApprovers: boolean): ProjectSettingsView => ({
    project_id: 'p1',
    repo_full_name: 'example-org/orders',
    settings: { ...base, approvers: ['octo'] },
    stored_invalid: false,
    can_edit: true,
    can_edit_approvers: canEditApprovers,
  })
  const input = { agentEnabled: false, testRunEnabled: true, approversText: 'octo, other' }

  it('owner は入力した承認者を送る', () => {
    expect(draftSettings(view(true), input)).toEqual({
      settings: { ...base, agent_enabled: false, approvers: ['octo', 'other'] },
      error: null,
    })
  })
  it('owner 以外は承認者を保存値のまま送る（入力は無視。API も変更を 403 で拒否する）', () => {
    expect(draftSettings(view(false), { ...input, approversText: '-bad' })).toEqual({
      settings: { ...base, agent_enabled: false, approvers: ['octo'] },
      error: null,
    })
  })
  it('owner の入力が不正なら理由を返す', () => {
    expect(draftSettings(view(true), { ...input, approversText: '-bad' }).error).not.toBeNull()
  })
})
