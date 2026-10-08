/**
 * AI が書いた本文を編集欄に流し込む判断（S76）。純粋関数だけで、状態は持たない。
 *
 * 途中経過は表示専用（編集欄は変えない）。確定したとき、送った時点から編集欄が変わっておらず、
 * 提案が妥当で付属ファイルに触れないときだけ、本文を一度に差し替える。提案の土台のハッシュは
 * 送った編集欄の本文のものなので、「送った本文と今の本文が同じ」ことは、ハッシュの一致と同じ意味。
 */

import { diffLines } from '@/services/textDiff'
import type { AppliedEdit, ApplyDecision, DecideApplyInput, DraftDiff, SkipReason } from '@/types/chatDraft'
import type { ArtifactContent } from '@/types/knowledge'
import type { RawProposal } from '@/types/knowledgeChat'

/** 本文の欄（提案が置き換える主な文字列）。スキルは SKILL.md。 */
export function bodyText(content: ArtifactContent): string {
  switch (content.kind) {
    case 'spec':
      return content.markdown
    case 'instructions':
      return content.body
    case 'skill':
      return content.skill_md
  }
}

/** 本文の欄だけを差し替える（名前・スコープ・付属ファイルなどは変えない）。 */
export function withBodyText(content: ArtifactContent, text: string): ArtifactContent {
  switch (content.kind) {
    case 'spec':
      return { ...content, markdown: text }
    case 'instructions':
      return { ...content, body: text }
    case 'skill':
      return { ...content, skill_md: text }
  }
}

function sameFiles(a: Record<string, string>, b: Record<string, string>): boolean {
  const ka = Object.keys(a)
  return ka.length === Object.keys(b).length && ka.every((k) => k in b && a[k] === b[k])
}

/** 提案がスキルの付属ファイルを（追加・変更・削除のどれかで）変えるか。 */
export function touchesFiles(content: ArtifactContent, raw: RawProposal): boolean {
  if (content.kind !== 'skill' || raw.files === undefined) return false
  return !sameFiles(raw.files, content.files)
}

/** 確定した提案を自動で本文に入れるか。入れない理由は、会話の 1 行に出す。 */
export function decideAutoApply({ proposal, sentEditorText, currentEditorText, touchesFiles: files, baseMatches }: DecideApplyInput): ApplyDecision {
  if (!proposal.valid) return { action: 'skip', reason: 'invalid' }
  if (files) return { action: 'skip', reason: 'files' }
  if (!baseMatches || sentEditorText !== currentEditorText) return { action: 'skip', reason: 'edited' }
  return { action: 'apply' }
}

/** 「元に戻す」できるか: 編集欄（とスキルの付属ファイル）が反映した直後のままのときだけ（その後の編集を消さない）。 */
export function canUndo(applied: AppliedEdit, currentEditorText: string, current?: ArtifactContent): boolean {
  if (applied.afterEditorText !== currentEditorText) return false
  if (applied.afterFiles && current?.kind === 'skill') return sameFiles(applied.afterFiles, current.files)
  return true
}

/** 反映前に戻す: 本文と、スキルなら付属ファイルも（名前・スコープなど反映が触らない欄はそのまま）。 */
export function restoreApplied(current: ArtifactContent, applied: AppliedEdit): ArtifactContent {
  const restored = withBodyText(current, applied.before)
  if (restored.kind === 'skill' && applied.beforeFiles) return { ...restored, files: { ...applied.beforeFiles } }
  return restored
}

/** 反映しなかった理由の文（診断のエラー件数は `invalid` のとき）。 */
export function skipMessage(reason: SkipReason, errors: number): string {
  switch (reason) {
    case 'invalid':
      return `診断のエラーがあるため反映していません（${errors} 件）`
    case 'files':
      return '付属ファイルの変更を含むため、確かめてから反映してください'
    case 'edited':
      return '編集中のため反映していません'
  }
}

/** 生成中の差分: 送った時点の本文と、届いた全文の行差分。大きすぎれば null。 */
export function draftDiff(before: string, draft: string): DraftDiff | null {
  const lines = diffLines(before, draft)
  if (lines === null) return null
  let anchor = -1
  lines.forEach((l, i) => {
    if (l.op === 'add') anchor = i
  })
  return { lines, anchor }
}
