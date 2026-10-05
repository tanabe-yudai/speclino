/** 仕様エディタの部品（components/spec-editor/）と知識の画面の props 型。 */

import type { ArtifactContent, ArtifactKind, ArtifactRevision, ArtifactScope, SpecDiagnostic, SpecPreview } from './knowledge'
import type { RequirementTrace } from '@/services/specPreview'
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

export interface DiagnosticsListProps {
  diagnostics: SpecDiagnostic[]
  /** 場所のある診断を押したとき（1 始まりの行・列） */
  onJump: (line: number, column: number) => void
}

export interface MermaidPreviewProps {
  markdown: string
  onJump: (line: number, column: number) => void
}

export interface CanonicalViewProps {
  canonical: string | null
  pending: boolean
  hasErrors: boolean
}

export interface RequirementTraceProps {
  trace: RequirementTrace
  hasCompiled: boolean
}

export interface ScopePickerProps {
  kind: ArtifactKind
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
  slug: string
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
  onApproved: (generation: number) => void
  onConflict: () => void
}
