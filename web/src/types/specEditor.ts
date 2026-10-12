/** 仕様エディタの部品（components/spec-editor/）と知識の画面の props 型。 */

import type { ArtifactContent, ArtifactRevision, ArtifactScope, SpecDiagnostic, SpecPreview } from './knowledge'
import type { ProjectSummary } from './project'

export interface MarkdownInputProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  /** 欄の説明・エラーの要素の id（aria-describedby） */
  describedBy?: string
  invalid?: boolean
  rows?: number
  textareaRef?: React.Ref<HTMLTextAreaElement>
}

export interface MarkdownPreviewProps {
  markdown: string
  /** ```mermaid のブロックを図として描く */
  mermaid?: boolean
  /** スキルの SKILL.md: frontmatter を見出しとリード文にして出す */
  skillMd?: boolean
}

export interface MarkdownSplitInputProps extends MarkdownInputProps {
  skillMd?: boolean
}

export interface DiagnosticsListProps {
  diagnostics: SpecDiagnostic[]
  /** 場所のある診断を押したとき（1 始まりの行・列） */
  onJump: (line: number, column: number) => void
}

export interface ScopePickerProps {
  scope: ArtifactScope
  onChange: (scope: ArtifactScope) => void
  projects: ProjectSummary[] | null
  error: string | null
}

export interface ContentEditorProps<C extends ArtifactContent = ArtifactContent> {
  content: C
  onChange: (content: C) => void
}

export interface SpecPaneProps extends ContentEditorProps<Extract<ArtifactContent, { kind: 'spec' }>> {
  preview: SpecPreview | null
  previewError: string | null
  previewPending: boolean
}

export interface ApprovalPanelProps {
  slug: string
  draft: ArtifactRevision | null
  dirty: boolean
  previewErrors: number
  previewPending: boolean
  previewFailed: boolean
  onApproved: (generation: number) => void
  onConflict: () => void
}

export interface RetirePanelProps {
  slug: string
  /** 今の有効な改訂（無ければパネルを出さない） */
  active: ArtifactRevision | null
  dirty: boolean
  onRetired: (generation: number) => void
  onConflict: () => void
}

export interface DiffViewProps {
  before: string
  after: string
  label: string
}

export interface ConflictDialogProps {
  open: boolean
  /** 読み直すと消える自分の本文（コピーの対象） */
  ownText: string
  onReload: () => void
  onClose: () => void
}

export interface PaneValidity {
  /** 欄の内容が読めず、保存・プレビューに使えない（JSON・付属ファイルのパス） */
  onInvalidChange: (invalid: boolean) => void
  /** 保存の応答で本文をサーバの内容に揃えるたびに増える。欄の文字列（JSON・付属ファイル）を作り直す合図 */
  resetKey: number
}

export interface SpecSideDiffProps {
  label: string
  /** 前の改訂があるか */
  previous: boolean
  /** undefined = まだ変換中、null = 診断にエラーがあり変換できない */
  before: string | null | undefined
  after: string | null | undefined
}
