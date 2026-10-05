/** project の設定とオンボーディングの型（S47）。src/speclino/domain/project_settings.py に対応させる。 */

export type GateFailVerdict = 'VIOLATED' | 'UNKNOWN' | 'REVIEW'

export interface ProjectSettings {
  fail_on: GateFailVerdict[]
  agent_enabled: boolean
  test_run_enabled: boolean
  approvers: string[]
  /** approvers と同じ順の GitHub ユーザー ID。サーバが保存のときに引いて埋める（送っても使われない）。 */
  approver_ids?: number[]
}

/** 承認者のログイン名と保存済みの GitHub ユーザー ID（未登録なら null）。 */
export interface ApproverAccount {
  login: string
  id: number | null
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
  /** 承認者がいるのに GitHub ユーザー ID が無い・足りない。ID の無い承認者は承認できない（保存し直す）。 */
  approver_ids_missing: boolean
  /** 承認者のログイン名と ID の組（ログイン名を他人が取っていないか確かめる表示用）。 */
  approvers: ApproverAccount[]
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
