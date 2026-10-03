/** project の設定とオンボーディングの型（S47）。src/speclino/domain/project_settings.py に対応させる。 */

export type GateFailVerdict = 'VIOLATED' | 'UNKNOWN' | 'REVIEW'

export interface ProjectSettings {
  fail_on: GateFailVerdict[]
  agent_enabled: boolean
  test_run_enabled: boolean
  approvers: string[]
}

export interface ProjectSettingsView {
  project_id: string
  repo_full_name: string
  settings: ProjectSettings
  /** 保存値が型に合わず、安全側の値（エージェント・test_run 停止）で動いている。 */
  stored_invalid: boolean
  can_edit: boolean
  /** PR の承認者を変えられるのは owner だけ。 */
  can_edit_approvers: boolean
}

export type OnboardingStepId = 'app' | 'speclino' | 'first_run'
/** done: 到達済み / todo: まだ / unknown: 手元のデータでは確かめられない。 */
export type OnboardingState = 'done' | 'todo' | 'unknown'

export interface OnboardingStep {
  id: OnboardingStepId
  state: OnboardingState
  detail: string
}

export interface ProjectOnboarding {
  project_id: string
  repo_full_name: string
  steps: OnboardingStep[]
}

export interface WorkspaceOnboarding {
  can_edit_settings: boolean
  projects: ProjectOnboarding[]
}
