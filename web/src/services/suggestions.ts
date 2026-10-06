/**
 * AI の書き換え提案（S67）を編集欄に当てる純関数。
 *
 * - サーバの位置は code point。受け取った時点で UTF-16（JS の文字列の添字）に直す
 * - 1 件を当てると後ろの位置がずれる。重ならない提案は位置をずらして残し（当てた範囲より後ろなら
 *   長さの差を足す、前ならそのまま）、重なる提案は外す。求め直しは不要
 * - 重なりの判定はサーバ（services/spec_suggest.py の overlaps）と同じ: 範囲が交わるか、同じ位置への挿入
 */

import type { PendingSuggestion, SuggestionExcerpt, SuggestionResult } from '@/types/suggestion'

/** code point の位置を UTF-16 の位置に直す。本文の外なら null。 */
export function codePointToUtf16(text: string, cp: number): number | null {
  let units = 0
  let points = 0
  while (points < cp) {
    if (units >= text.length) return null
    const code = text.codePointAt(units) ?? 0
    units += code > 0xffff ? 2 : 1
    points++
  }
  return units
}

/** サーバの提案を、編集欄の本文（提案を求めたときの本文）に当てられる形にする。 */
export function toPending(text: string, results: SuggestionResult[]): PendingSuggestion[] {
  const out: PendingSuggestion[] = []
  results.forEach((r, id) => {
    const start = codePointToUtf16(text, r.start)
    const end = codePointToUtf16(text, r.end)
    if (start === null || end === null || start > end) return
    out.push({ id, start, end, replacement: r.replacement, reason: r.reason, after: r.after })
  })
  return out
}

export function overlaps(a: PendingSuggestion, b: PendingSuggestion): boolean {
  return (a.start < b.end && b.start < a.end) || a.start === b.start
}

export function applySuggestion(text: string, s: PendingSuggestion): string {
  return text.slice(0, s.start) + s.replacement + text.slice(s.end)
}

/** applied を当てた後の残りの提案（位置をずらしたもの）と、重なって外した件数。 */
export function afterApply(list: PendingSuggestion[], applied: PendingSuggestion): { remaining: PendingSuggestion[]; dropped: number } {
  const delta = applied.replacement.length - (applied.end - applied.start)
  const remaining: PendingSuggestion[] = []
  let dropped = 0
  for (const s of list) {
    if (s.id === applied.id) continue
    if (overlaps(s, applied)) {
      dropped++
    } else if (s.start >= applied.end) {
      remaining.push({ ...s, start: s.start + delta, end: s.end + delta })
    } else {
      remaining.push(s)
    }
  }
  return { remaining, dropped }
}

const countNewlines = (text: string, upTo: number) => {
  let n = 0
  for (let i = text.indexOf('\n'); i !== -1 && i < upTo; i = text.indexOf('\n', i + 1)) n++
  return n
}

function lineStart(text: string, line: number): number {
  let pos = 0
  for (let k = 0; k < line; k++) {
    const nl = text.indexOf('\n', pos)
    if (nl === -1) return text.length
    pos = nl + 1
  }
  return pos
}

function lineEnd(text: string, line: number): number {
  const nl = text.indexOf('\n', lineStart(text, line))
  return nl === -1 ? text.length : nl
}

const trimNewline = (s: string) => (s.endsWith('\n') ? s.slice(0, -1) : s)

/** 提案が変える行と、前後 context 行（差分の表示に使う）。 */
export function excerpt(text: string, s: PendingSuggestion, context = 2): SuggestionExcerpt {
  const first = Math.max(0, countNewlines(text, s.start) - context)
  const last = (s.end > s.start ? countNewlines(text, s.end - 1) : countNewlines(text, s.start)) + context
  const from = lineStart(text, first)
  const to = Math.max(lineEnd(text, last), s.end)
  return {
    before: trimNewline(text.slice(from, to)),
    after: trimNewline(text.slice(from, s.start) + s.replacement + text.slice(s.end, to)),
    firstLine: first + 1,
  }
}
