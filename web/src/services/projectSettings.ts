/** project の設定とオンボーディングの表示ロジック（S47）。コンポーネントには埋め込まない。 */

import type {
  OnboardingState,
  OnboardingStep,
  OnboardingStepId,
  ProjectSettings,
  ProjectSettingsView,
} from '@/types/projectSettings'

// API（src/speclino/domain/project_settings.py）と同じ規則。bot（`[bot]`）は承認者にしない
const GITHUB_LOGIN = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/

export const ONBOARDING_STEP_TITLES: Record<OnboardingStepId, string> = {
  app: 'GitHub App を入れてリポジトリを登録する',
  speclino: '.speclino/ と承認済みの仕様を置く',
  first_run: 'PR を開いて最初の検査を受ける',
}

export const ONBOARDING_STATE_LABELS: Record<OnboardingState, string> = {
  done: '完了',
  todo: 'まだ',
  unknown: '未確認',
}

/** すべての段階が完了しているか。未確認は完了に数えない（推測で埋めない）。 */
export function onboardingDone(steps: OnboardingStep[]): boolean {
  return steps.length > 0 && steps.every((s) => s.state === 'done')
}

/** 承認者の入力（カンマ・空白・改行区切り）を GitHub のログイン名の並びにする。 */
export function parseApprovers(text: string): { approvers: string[]; error: string | null } {
  const approvers = text.split(/[\s,]+/).filter(Boolean)
  const bad = approvers.filter((login) => !GITHUB_LOGIN.test(login))
  if (bad.length > 0) return { approvers, error: `GitHub のログイン名ではありません: ${bad.join(', ')}` }
  if (new Set(approvers).size !== approvers.length) return { approvers, error: '承認者が重複しています' }
  return { approvers, error: null }
}

export function settingsEqual(a: ProjectSettings, b: ProjectSettings): boolean {
  return (
    a.agent_enabled === b.agent_enabled &&
    a.test_run_enabled === b.test_run_enabled &&
    a.fail_on.join(',') === b.fail_on.join(',') &&
    a.approvers.join(',') === b.approvers.join(',')
  )
}

export interface SettingsInput {
  agentEnabled: boolean
  testRunEnabled: boolean
  approversText: string
}

/**
 * 画面の入力から保存する設定を作る。PR の承認者は owner（`can_edit_approvers`）だけが変えられるので、
 * それ以外は保存値のまま送る（API も承認者の変更を 403 で拒否する）。
 */
export function draftSettings(
  view: ProjectSettingsView,
  input: SettingsInput,
): { settings: ProjectSettings; error: string | null } {
  const parsed = view.can_edit_approvers
    ? parseApprovers(input.approversText)
    : { approvers: view.settings.approvers, error: null }
  return {
    settings: {
      fail_on: view.settings.fail_on,
      agent_enabled: input.agentEnabled,
      test_run_enabled: input.testRunEnabled,
      approvers: parsed.approvers,
    },
    error: parsed.error,
  }
}
