/** 判定・状態を日本語ラベルへ変換する定数群。コンポーネントには埋め込まない。 */

import type {
  AssessmentOutcome,
  BaseHeadStatus,
  Category,
  Severity,
  TestObservation,
  Verdict,
} from '../types/finding'
import type { OnMissingInput, PolicyRevision } from '../types/policy'
import type { ApprovalState, NextActionKind, PolicyLevel, RequirementFilter } from '../types/project'
import type { ExecState, GateStatus } from '../types/run'
import type { VerdictTone } from '../types/components'
import type { WorkspaceRole } from '../types/me'
import type { SettingsTab } from '../types/command'
import type { KnowledgeKind, KnowledgeState, ProjectTab, RunGateFilter, SectionId } from '../types/navigation'

export const VERDICT_LABELS: Record<Verdict, string> = {
  VERIFIED_IN_SCOPE: '適合',
  VIOLATED: '違反',
  UNKNOWN: '未検証',
  REVIEW: '要確認',
}

/** ゲートを止めない REVIEW（人が問題なしと確定した指摘）の区分。verdict は REVIEW のまま */
export const CLEARED_LABEL = '確定済み・情報'

/** VIOLATED → REVIEW → UNKNOWN → VERIFIED_IN_SCOPE の表示順。 */
export const VERDICT_ORDER: Record<Verdict, number> = {
  VIOLATED: 0,
  REVIEW: 1,
  UNKNOWN: 2,
  VERIFIED_IN_SCOPE: 3,
}

export const CATEGORY_LABELS: Record<Category, string> = {
  missing_effect: '効果の欠落',
  forbidden_effect: '禁止された効果',
  constraint_mismatch: '制約の不一致',
  state_mismatch: '状態の不一致',
  outcome_mismatch: '結果の不一致',
  argument_mismatch: '引数の不一致',
  cardinality_mismatch: '回数の不一致',
  undocumented_behavior: '未記載の挙動',
  unsupported_analysis: '解析対象外',
  ambiguous_spec: '仕様の曖昧さ',
  stale_approval: '承認の失効',
  vacuous_applicability: '適用条件の充足不能',
  potential_violation: '違反の疑い',
  solver_limit: '検査上限到達',
  conformant: '適合',
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  high: '高',
  medium: '中',
  low: '低',
  info: '情報',
}

export const TEST_OBSERVATION_LABELS: Record<TestObservation, string> = {
  OBSERVED: '観測済み',
  NOT_OBSERVED: '未観測',
  CONTRADICTED: '矛盾',
}

export const EXEC_STATE_LABELS: Record<ExecState, string> = {
  queued: '待機中',
  running: '実行中',
  awaiting_answer: '回答待ち',
  completed: '完了',
  failed: '実行失敗',
  cancelled: 'キャンセル',
  superseded: '無効化（再実行あり）',
}

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  pass: '通過',
  pass_with_exceptions: '通過（免除中あり）',
  fail: '失敗',
}

interface EventMeta {
  label: string
  /** Timeline のインジケータの色分け用カテゴリ。 */
  group: 'state' | 'agent' | 'tool' | 'audit' | 'other'
}

const EVENT_TYPE_META: Record<string, EventMeta> = {
  state: { label: '状態遷移', group: 'state' },
  'agent.start': { label: 'エージェント開始', group: 'agent' },
  'agent.end': { label: 'エージェント終了', group: 'agent' },
  plan: { label: '計画', group: 'agent' },
  'tool.policy_check': { label: 'ツール実行: 権限確認', group: 'tool' },
  'tool.analyze_focus': { label: 'ツール実行: 重点再解析', group: 'tool' },
  'tool.repo_search': { label: 'ツール実行: リポジトリ検索', group: 'tool' },
  audit: { label: '監査記録', group: 'audit' },
  spec_bot: { label: '仕様 PR bot', group: 'other' },
}

export function eventMeta(type: string): EventMeta {
  return EVENT_TYPE_META[type] ?? { label: type, group: 'other' }
}

export const BASE_HEAD_STATUS_LABELS: Record<BaseHeadStatus, string> = {
  new: '新規',
  existing: '既存',
  resolved: '解消',
  unchanged: '変化なし',
}

/** base との比較の色（変化なしは出さない）。 */
export const BASE_HEAD_STATUS_TONE: Record<Exclude<BaseHeadStatus, 'unchanged'>, VerdictTone> = {
  new: 'bad',
  existing: 'rev',
  resolved: 'ok',
}

export const WORKSPACE_ROLE_LABELS: Record<WorkspaceRole, string> = {
  owner: 'オーナー',
  approver: '承認者',
  member: 'メンバー',
}

/** ゲートの結果を判定の色に対応させる（通過=適合の色、通過（免除中あり）=免除中の色、失敗=違反の色）。 */
export function gateTone(status: GateStatus): VerdictTone {
  return status === 'pass' ? 'ok' : status === 'pass_with_exceptions' ? 'exempt' : 'bad'
}

/** 要求の承認（仕様の要求ごと）。知識の「未承認」（有効な改訂が無い）と混ぜないよう「承認待ち」と呼ぶ。 */
export const APPROVAL_LABELS: Record<ApprovalState, string> = {
  approved: '承認済み',
  unapproved: '承認待ち',
  stale: '承認切れ',
}

export const POLICY_LEVEL_LABELS: Record<PolicyLevel, string> = { mandatory: '必須', advisory: '助言' }

export const REQUIREMENT_FILTER_LABELS: Record<RequirementFilter, string> = {
  all: 'すべて',
  violated: '違反',
  unknown: '未検証',
  review: '要確認',
  unapproved: '承認待ち',
}

/** 「次にやること」の種類の名前と色（プロジェクト画面とランディングページの見本で共通）。 */
export const NEXT_ACTION_KIND: Record<NextActionKind, { label: string; tone: VerdictTone }> = {
  violated: { label: '違反', tone: 'bad' },
  stale: { label: '承認切れ', tone: 'rev' },
  review: { label: '要確認', tone: 'rev' },
  exempted: { label: '免除中', tone: 'rev' },
  unknown: { label: '未検証', tone: 'unk' },
  unapproved: { label: '承認待ちの要求', tone: 'rev' },
}

export const ON_MISSING_INPUT_LABELS: Record<OnMissingInput, string> = {
  violation: '対象が無ければ違反',
  unknown: '対象が無ければ未検証',
  skipped: '対象が無ければ評価しない',
}

export const REVISION_STATUS_LABELS: Record<PolicyRevision['status'], string> = {
  active: '有効',
  draft: '下書き',
  superseded: '置き換え済み',
}

/** 規約の差分で変わった項目の名前。 */
export const RULE_FIELD_LABELS: Record<string, string> = {
  level: 'レベル',
  title: '題',
  text: '本文',
  applies_to: '適用範囲',
  verifier: '検査器',
  on_missing_input: '対象が無いとき',
  exceptions_allowed: '例外の可否',
  version: '版',
}

/** 監査の「何を」（action と decision の組）。 */
export function auditLabel(action: string, decision: string): string {
  if (action === 'policy.draft') return decision === 'updated' ? '下書きを更新' : '下書きを作成'
  if (action === 'policy.approve') return decision === 'denied' ? '承認を拒否（承認者ではない）' : '承認して有効にした'
  return `${action} ${decision}`
}

/**
 * LLM 支援の規約の区分（S70）。指摘なしは機械検証ではないことを明記する独自の区分。
 * 指摘なしも人の確定まではゲートを止める（#114）ので「人の確定が必要」と出す。
 */
export const ASSESSMENT_OUTCOME_LABELS: Record<AssessmentOutcome, string> = {
  issue: 'AI の指摘あり（人の確定待ち）',
  split: 'AI の評価が割れた（人の確定待ち）',
  no_issue: 'AI で確認、指摘なし（機械検証ではない）— 人の確定が必要',
}

/** 確定済み（ゲートの cleared）の LLM 支援の区分。確定待ちの文言を外す。 */
export const ASSESSMENT_OUTCOME_CLEARED_LABELS: Record<AssessmentOutcome, string> = {
  issue: 'AI の指摘あり（人が問題なしと確定）',
  split: 'AI の評価が割れた（人が問題なしと確定）',
  no_issue: 'AI で確認、指摘なし（機械検証ではない）— 人が確定',
}

export function assessmentOutcomeLabel(outcome: AssessmentOutcome, cleared: boolean): string {
  return (cleared ? ASSESSMENT_OUTCOME_CLEARED_LABELS : ASSESSMENT_OUTCOME_LABELS)[outcome]
}

export const ASSESSMENT_OUTCOME_TONE: Record<AssessmentOutcome, VerdictTone> = {
  issue: 'rev',
  split: 'rev',
  no_issue: 'rev',
}

/** 検査のきっかけ（review_runs.trigger）。 */
export const TRIGGER_LABELS: Record<string, string> = {
  'pull_request.opened': 'PR が開かれた',
  'pull_request.synchronize': 'PR にコミットが追加された',
  'pull_request.reopened': 'PR が再び開かれた',
  'pull_request.ready_for_review': 'PR がレビュー待ちになった',
  rerequested: 'GitHub から再実行が求められた',
  comment: 'PR のコメント',
  rerun: '画面からの再実行',
  attestation: '人の確定の反映',
  pr_review: 'PR の Approve（人の確定）',
  mcp: 'エージェントからの依頼',
  a2a: 'エージェントからの依頼',
  manual: '手動',
}

/** 知識の同期・対応表・旧形式の仕様の検査（システムが付ける規約）の、人が読む題と対処。 */
export const SYSTEM_RULES: Record<string, { title: string; hint: string }> = {
  'KNOWLEDGE-LOCK-001': {
    title: 'リポジトリの知識が最新ではありません',
    hint: 'リポジトリで同期を実行して最新にしてください',
  },
  'KNOWLEDGE-BINDINGS-001': {
    title: '仕様とコードの対応表が変わりました',
    hint: '変更を確認し、承認者が PR で確定してください',
  },
  'KNOWLEDGE-SPEC-001': {
    title: 'リポジトリに旧形式の仕様が残っています',
    hint: '画面に取り込んで承認してください',
  },
}

/** 「ID」または「ID:対象」の ID 部分に対応するシステム規約の題。無ければ null。 */
export function systemRuleTitle(requirementId: string): string | null {
  return SYSTEM_RULES[requirementId.split(':')[0]]?.title ?? null
}

/** AI と作る会話の、返答の出典の種類（S81）。 */
export const CHAT_SOURCE_LABELS: Record<'knowledge' | 'repo' | 'web', string> = {
  knowledge: '承認済みの知識',
  repo: 'リポジトリ',
  web: 'Web',
}

// ---------------------------------------------------------------- 画面の行き来（用語はここにそろえる）

/** 第 1 階層の名前。「検査」は run（ゲートは検査の判定の言葉）、「観測」は可観測性の画面。 */
export const SECTION_LABELS: Record<SectionId, string> = {
  home: 'ホーム',
  specs: '仕様',
  skills: 'スキル',
  instructions: '指示書',
  policies: '規約',
  runs: '検査',
  observability: '観測',
  settings: '設定',
}

/** スマホの画面下のタブの 5 つ目（スキル・指示書・規約・設定・ワークスペースの切り替え）。 */
export const MORE_LABEL = 'その他'

export const PROJECT_TAB_LABELS: Record<ProjectTab, string> = {
  overview: '概要',
  specs: '仕様',
  runs: '検査',
}

/** 種類ごとの画面の説明（見出しの下）。規約の画面は自分で書く。 */
export const KNOWLEDGE_PAGE_DESCRIPTIONS: Record<Exclude<KnowledgeKind, 'policy'>, string> = {
  spec: '要求と振る舞いの仕様。ワークスペース全体で共有するものと、リポジトリ固有のものに分けて並べます。改訂して承認すると、検査と各自のエージェントに使われます。',
  skill: 'エージェントに渡す手順（SKILL.md）。改訂して承認すると、各自のエージェントに配られます。',
  instructions: 'エージェントへの指示書（AGENTS.md / CLAUDE.md に書き出す本文）。改訂して承認すると、各自のエージェントに配られます。',
}

/** 仕様の画面の節。 */
export const SPEC_GROUP_LABELS = {
  workspace: 'ワークスペース全体の仕様',
  repositories: 'リポジトリ別の仕様',
  thisRepository: 'このリポジトリの仕様',
  unknownRepository: 'このワークスペースに無いリポジトリ',
} as const

/** 仕様の節の説明。 */
export const SPEC_GROUP_HINTS = {
  workspace: 'すべてのリポジトリに当たる仕様（要求仕様など）。',
  repositories: '特定のリポジトリに当たる仕様（振る舞いなどの細かい仕様）。複数のリポジトリに当たるものはそれぞれに出します。',
} as const

/** 取り下げ済みの知識の戻し方（設定の「取り下げ済みの知識」）。 */
export const RETIRED_RESTORE_HINT =
  '取り下げた知識は配信と検査に使われません。戻すときはエディタを開き、下書きを保存してオーナーか承認者が承認すると、その改訂が有効になって戻ります。'

export const AGENT_CONNECTION_LABEL = 'エージェント接続（MCP / A2A）'
export const REPO_SYNC_LABEL = 'リポジトリ同期（speclino sync）'

export const SETTINGS_TAB_LABELS: Record<SettingsTab, string> = {
  members: 'メンバー',
  repositories: 'リポジトリ',
  agents: 'エージェント接続',
  projects: 'プロジェクト',
  retired: '取り下げ済みの知識',
}

/** 知識の種類（規約はチーム規約）。 */
export const KNOWLEDGE_KIND_LABELS: Record<KnowledgeKind, string> = {
  spec: '仕様',
  skill: 'スキル',
  instructions: '指示書',
  policy: '規約',
}

/** 知識の状態。未承認=有効な改訂がまだ無い、承認待ち=承認を待つ下書きがある。 */
export const KNOWLEDGE_STATE_LABELS: Record<KnowledgeState, string> = {
  active: '有効',
  pending: '承認待ち',
  unapproved: '未承認',
  retired: '取り下げ済み',
}

export const RUN_GATE_FILTER_LABELS: Record<RunGateFilter, string> = {
  fail: '失敗',
  pass_with_exceptions: '通過（免除中あり）',
  pass: '通過',
  running: '実行中・未判定',
}

/** AI と作る会話の、質問と選択肢の表示（返答の下に出し、選択肢を押すと発言として送る）。 */
export const CHAT_QUESTION_LABELS = {
  heading: 'AI からの質問',
  hint: '選択肢を押すとそのまま送ります。自由に書いて答えることもできます。',
} as const
