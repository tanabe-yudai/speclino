/** 仕様エディタの表示のための純関数（診断の並び、行への移動、図の切り出し、要求の追跡）。 */

import type { CompiledSpec, RequirementRow, SpecDiagnostic } from '@/types/knowledge'

/** エラーを先に、同じ重さの中では行・列の順。場所の無いものはその重さの最後。 */
export function sortDiagnostics(diagnostics: SpecDiagnostic[]): SpecDiagnostic[] {
  const rank = (d: SpecDiagnostic) => (d.severity === 'error' ? 0 : 1)
  const pos = (d: SpecDiagnostic) => (d.span ? d.span.line * 1e6 + d.span.column : Number.MAX_SAFE_INTEGER)
  return [...diagnostics].sort((x, y) => rank(x) - rank(y) || pos(x) - pos(y))
}

export function countErrors(diagnostics: SpecDiagnostic[]): number {
  return diagnostics.filter((d) => d.severity === 'error').length
}

/** 1 始まりの行・列 → 文字の位置。範囲外は本文・行の終わりに寄せる。 */
export function lineOffset(text: string, line: number, column: number): number {
  const lines = text.split('\n')
  if (line > lines.length) return text.length
  let offset = 0
  for (let i = 0; i < line - 1; i++) offset += lines[i].length + 1
  return offset + Math.min(Math.max(column - 1, 0), lines[line - 1].length)
}

export interface MermaidBlock {
  index: number
  /** ```mermaid の行（1 始まり） */
  line: number
  /** `%%@ id:` の宣言（無ければ null） */
  id: string | null
  source: string
}

/**
 * 描画のための ```mermaid ブロックの切り出し。正規形のフェンス（行頭の ```mermaid と ```）だけを見る。
 * どれを図として読むかの正は parser（S63）で、ここは見た目の確認にだけ使う。
 */
export function mermaidBlocks(markdown: string): MermaidBlock[] {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const blocks: MermaidBlock[] = []
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trimEnd() !== '```mermaid') continue
    const close = lines.findIndex((l, k) => k > i && /^```\s*$/.test(l))
    if (close < 0) break
    const body = lines.slice(i + 1, close)
    const header = body.map((l) => /^\s*%%@\s*id:\s*(\S+)\s*$/.exec(l)).find(Boolean)
    blocks.push({ index: blocks.length, line: i + 1, id: header ? header[1] : null, source: body.join('\n') })
    i = close
  }
  return blocks
}

export interface TraceRow extends RequirementRow {
  formalized: boolean
  /** mandatory で未形式化（ゲートで REVIEW のまま残る） */
  gateReview: boolean
}

export interface RequirementTrace {
  rows: TraceRow[]
  formalized: number
  total: number
  gateReview: number
}

export function requirementTrace(compiled: CompiledSpec | null): RequirementTrace {
  const rows = (compiled?.requirements ?? []).map((r) => {
    const formalized = r.formalized_by.length > 0
    return { ...r, formalized, gateReview: !formalized && r.level === 'mandatory' }
  })
  return {
    rows,
    formalized: rows.filter((r) => r.formalized).length,
    total: rows.length,
    gateReview: rows.filter((r) => r.gateReview).length,
  }
}

type JsonShape = 'object' | 'array'

/** 旧形式の入口の JSON 欄。空なら object は null、array は []。 */
export function parseJsonField(
  text: string,
  shape: JsonShape,
): { value: Record<string, unknown> | Record<string, unknown>[] | null; error: string | null } {
  if (!text.trim()) return { value: shape === 'array' ? [] : null, error: null }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { value: null, error: 'JSON として読めません' }
  }
  if (shape === 'array') {
    if (!Array.isArray(parsed)) return { value: null, error: 'JSON の配列にしてください' }
    return { value: parsed as Record<string, unknown>[], error: null }
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { value: null, error: 'JSON のオブジェクトにしてください' }
  }
  return { value: parsed as Record<string, unknown>, error: null }
}
