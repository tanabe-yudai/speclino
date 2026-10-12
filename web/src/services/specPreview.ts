/** 仕様エディタの表示のための純関数（診断の並び、行への移動、旧形式の JSON 欄）。 */

import type { SpecDiagnostic } from '@/types/knowledge'

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
