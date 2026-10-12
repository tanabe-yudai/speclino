/** 知識（spec / skill / instructions）の画面の判断。表示はコンポーネント、通信は api/knowledge.ts。 */

import { ApiError } from '@/api/errors'
import type {
  ApprovalState,
  ArtifactContent,
  ArtifactKind,
  ArtifactRevision,
  ArtifactScope,
  KnowledgeSection,
  RetireState,
} from '@/types/knowledge'
import type { WorkspaceRole } from '@/types/me'

export const SECTIONS: KnowledgeSection[] = ['specs', 'skills', 'instructions']

const SECTION_KIND: Record<KnowledgeSection, ArtifactKind> = {
  specs: 'spec',
  skills: 'skill',
  instructions: 'instructions',
}

export const KIND_SECTION: Record<ArtifactKind, KnowledgeSection> = {
  spec: 'specs',
  skill: 'skills',
  instructions: 'instructions',
}

export const KIND_LABELS: Record<ArtifactKind, string> = {
  spec: '仕様',
  skill: 'スキル',
  instructions: '指示書',
}

export const STATUS_LABELS = { draft: '下書き', active: '有効', superseded: '置き換え済み' } as const

export function parseSection(section: string | undefined): ArtifactKind | null {
  return section && section in SECTION_KIND ? SECTION_KIND[section as KnowledgeSection] : null
}

export function emptyContent(kind: ArtifactKind): ArtifactContent {
  switch (kind) {
    case 'spec':
      return { kind, markdown: '', domain: null, legacy_ir: [], scope: { workspace: false, projects: [] }, compiled: null }
    case 'skill':
      return {
        kind,
        skill_md: '---\nname: \ndescription: \n---\n\n',
        files: {},
        scope: { workspace: true, projects: [] },
      }
    case 'instructions':
      return { kind, body: '', scope: { workspace: true, projects: [] } }
  }
}

export function activeRevision(revisions: ArtifactRevision[]): ArtifactRevision | null {
  return revisions.find((r) => r.status === 'active') ?? null
}

/** 取り下げ済みなら取り下げた改訂（有効な改訂が無く、承認済みの改訂がある。revisions は新しい順）。 */
export function retiredRevision(revisions: ArtifactRevision[]): ArtifactRevision | null {
  if (activeRevision(revisions)) return null
  return revisions.find((r) => r.status === 'superseded') ?? null
}

export interface EditBase {
  base: ArtifactRevision | null
  expectedRevision: number | null
  expectedHash: string | null
}

/**
 * 編集の土台: 下書きがあれば下書き、無ければ有効な改訂、取り下げ済みなら最新の改訂（サーバも同じ規則で
 * 次の番号の下書きを作る）。保存の前提（番号と hash）も同じもの。
 */
export function editBase(draft: ArtifactRevision | null, revisions: ArtifactRevision[]): EditBase {
  const base = draft ?? activeRevision(revisions) ?? revisions[0] ?? null
  return { base, expectedRevision: base?.revision ?? null, expectedHash: base?.content_hash ?? null }
}

export function scopeProblem(kind: ArtifactKind, scope: ArtifactScope): string | null {
  if (kind === 'spec') {
    return scope.workspace || scope.projects.length === 0 ? '仕様のスコープはプロジェクトを 1 つ以上選びます' : null
  }
  return scope.workspace === scope.projects.length > 0 ? 'ワークスペース全体か、プロジェクトを 1 つ以上選びます' : null
}

/** 内容の比較（キーの順に依らない）。 */
export function isSameContent(a: ArtifactContent, b: ArtifactContent): boolean {
  return stableJson(a) === stableJson(b)
}

function stableJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stableJson).join(',')}]`
  if (v && typeof v === 'object') {
    const entries = Object.entries(v as Record<string, unknown>).sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0))
    return `{${entries.map(([k, val]) => `${JSON.stringify(k)}:${stableJson(val)}`).join(',')}}`
  }
  return JSON.stringify(v ?? null)
}

export interface ApprovalInput {
  role: WorkspaceRole | null
  uid: string | null
  draft: ArtifactRevision | null
  dirty: boolean
  /** spec のとき: 保存した本文のプレビューのエラー数 */
  previewErrors: number
  previewPending: boolean
  /** spec のとき: プレビュー API が失敗した（診断が分からないので承認させない） */
  previewFailed: boolean
}

/** 承認のボタン。承認するのは保存済みの下書き（サーバが番号と hash で照合する）。 */
export function approvalState(input: ApprovalInput): ApprovalState {
  const visible = input.role === 'owner' || input.role === 'approver'
  const selfApproval = !!input.draft && input.draft.author === input.uid
  const blocked = (reason: string): ApprovalState => ({ visible, enabled: false, reason, selfApproval })
  if (!input.draft) return blocked('承認する下書きがありません')
  if (input.dirty) return blocked('保存していない変更があります。保存してから承認します')
  if (input.draft.kind === 'spec') {
    if (input.previewFailed) return blocked('診断を取得できませんでした')
    if (input.previewPending) return blocked('保存した本文の診断を確かめています')
    if (input.previewErrors > 0) return blocked(`診断にエラーが ${input.previewErrors} 件あります`)
  }
  return { visible, enabled: true, reason: null, selfApproval }
}

/** 取り下げのボタン。取り下げるのは今の有効な改訂（サーバが番号と hash で照合する）。 */
export function retireState(role: WorkspaceRole | null, active: ArtifactRevision | null, dirty: boolean): RetireState {
  const visible = !!active && (role === 'owner' || role === 'approver')
  if (dirty) return { visible, enabled: false, reason: '保存していない変更があります。保存するか戻してから取り下げます' }
  return { visible, enabled: visible, reason: null }
}

export type KnowledgeErrorKind = 'conflict' | 'bundle_conflict' | 'not_compilable' | 'forbidden' | 'invalid' | 'other'

/** 保存・承認の失敗を画面の区分と文言にする。conflict は読み直しを促すダイアログで扱う。 */
export function describeKnowledgeError(err: unknown): { kind: KnowledgeErrorKind; message: string } {
  if (!(err instanceof ApiError)) return { kind: 'other', message: '通信に失敗しました。時間をおいて、もう一度試してください' }
  switch (err.code) {
    case 'draft_changed':
      return { kind: 'conflict', message: '他の人が先に保存または承認しました' }
    case 'bundle_conflict':
      return { kind: 'bundle_conflict', message: `ほかの有効な内容と競合するため承認できません: ${err.message}` }
    case 'spec_not_compilable':
      return { kind: 'not_compilable', message: `仕様を読み取れないため承認できません: ${err.message}` }
    case 'not_an_approver':
      return { kind: 'forbidden', message: '承認・取り下げができるのはオーナーと承認者だけです' }
    case 'artifact_not_active':
      return { kind: 'conflict', message: '有効な改訂がありません（すでに取り下げられています）' }
    case 'artifact_exists':
      return { kind: 'invalid', message: '同じ名前のものが既にあります' }
    case 'invalid_artifact':
    case 'validation_error':
      return { kind: 'invalid', message: err.message }
    default:
      return { kind: 'other', message: err.message }
  }
}

/** 編集の土台にする内容。承認済みの spec の compiled は下書きに入れられない（送ると 422）ので外す。 */
export function toEditable(content: ArtifactContent): ArtifactContent {
  return content.kind === 'spec' && content.compiled !== null ? { ...content, compiled: null } : content
}

function scopeLine(scope: ArtifactScope, projectNames: Map<string, string>): string {
  if (scope.workspace) return 'スコープ: ワークスペース全体'
  return `スコープ: ${scope.projects.map((p) => projectNames.get(p) ?? p).join(', ')}`
}

/** 改訂の差分に使う 1 つの本文（スコープ・付属ファイルも行として並べ、変更が差分に出るようにする）。 */
export function revisionText(content: ArtifactContent, projectNames: Map<string, string>): string {
  const head = scopeLine(content.scope, projectNames)
  switch (content.kind) {
    case 'spec':
      return `${head}\n\n${content.markdown}`
    case 'instructions':
      return `${head}\n\n${content.body}`
    case 'skill': {
      const files = Object.keys(content.files)
        .sort()
        .map((path) => `=== ${path}\n${content.files[path]}`)
      return [`${head}\n\n=== SKILL.md\n${content.skill_md}`, ...files].join('\n\n')
    }
  }
}

/**
 * 保存の応答を受けたときの本文。送信から応答までに入力があれば、それを消さずに残す
 * （base だけが進み、画面は dirty のまま）。入力が無ければサーバの内容に揃える。
 */
export function contentAfterSave(
  sent: ArtifactContent,
  current: ArtifactContent,
  saved: ArtifactRevision,
): { content: ArtifactContent; replaced: boolean } {
  return isSameContent(sent, current) ? { content: toEditable(saved.content), replaced: true } : { content: current, replaced: false }
}
