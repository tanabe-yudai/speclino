import { describe, expect, it } from 'vitest'
import { afterApply, applySuggestion, codePointToUtf16, excerpt, overlaps, toPending } from './suggestions'
import type { PendingSuggestion, SuggestionOutcome, SuggestionResult } from '@/types/suggestion'

const outcome: SuggestionOutcome = { errors_before: 1, errors_after: 0, compiles: true, diagnostics_after: [] }
const result = (start: number, end: number, replacement: string, reason = 'r'): SuggestionResult => ({ start, end, replacement, reason, after: outcome })
const pending = (id: number, start: number, end: number, replacement: string): PendingSuggestion => ({ id, start, end, replacement, reason: `r${id}`, after: outcome })

describe('codePointToUtf16', () => {
  it('counts surrogate pairs as one code point', () => {
    const text = '😀あ\nx'
    expect(codePointToUtf16(text, 0)).toBe(0)
    expect(codePointToUtf16(text, 1)).toBe(2)
    expect(codePointToUtf16(text, 3)).toBe(4)
    expect(text.slice(codePointToUtf16(text, 3) ?? -1)).toBe('x')
  })

  it('returns null past the end of the text', () => {
    expect(codePointToUtf16('ab', 2)).toBe(2)
    expect(codePointToUtf16('ab', 3)).toBeNull()
  })
})

describe('toPending', () => {
  it('converts server offsets (code points) to editor offsets (UTF-16) and numbers them', () => {
    const text = '😀\nold\n'
    const [s] = toPending(text, [result(2, 6, 'new\n')])
    expect(s.id).toBe(0)
    expect(text.slice(s.start, s.end)).toBe('old\n')
    expect(applySuggestion(text, s)).toBe('😀\nnew\n')
  })

  it('drops suggestions whose range is outside the text', () => {
    expect(toPending('ab', [result(1, 5, 'x')])).toEqual([])
  })
})

describe('overlaps', () => {
  it('treats intersecting ranges and inserts at the same place as overlapping', () => {
    expect(overlaps(pending(0, 0, 4, ''), pending(1, 3, 6, ''))).toBe(true)
    expect(overlaps(pending(0, 0, 4, ''), pending(1, 4, 6, ''))).toBe(false)
    expect(overlaps(pending(0, 2, 2, 'a'), pending(1, 2, 2, 'b'))).toBe(true)
    expect(overlaps(pending(0, 2, 2, 'a'), pending(1, 0, 5, ''))).toBe(true)
  })
})

describe('afterApply', () => {
  const text = 'line1\nline2\nline3\nline4\n'
  const first = pending(0, 0, 6, 'L1\n') // line1 → L1（3 文字短くなる）
  const later = pending(1, 12, 18, 'LINE3\n')
  const overlapping = pending(2, 3, 9, 'x')

  it('shifts later suggestions by the change in length and keeps earlier ones as they are', () => {
    const applied = applySuggestion(text, later)
    const { remaining, dropped } = afterApply([first, later, overlapping], later)
    expect(dropped).toBe(0)
    expect(remaining.map((s) => s.id)).toEqual([0, 2])
    // later の後で first を当てても、最初から両方を当てた結果と同じ
    const both = applySuggestion(applied, remaining[0])
    expect(both).toBe('L1\nline2\nLINE3\nline4\n')
  })

  it('drops suggestions that overlap the applied one', () => {
    const { remaining, dropped } = afterApply([first, later, overlapping], first)
    expect(dropped).toBe(1)
    expect(remaining.map((s) => s.id)).toEqual([1])
    const shifted = remaining[0]
    expect([shifted.start, shifted.end]).toEqual([9, 15])
    expect(applySuggestion(applySuggestion(text, first), shifted)).toBe('L1\nline2\nLINE3\nline4\n')
  })
})

describe('excerpt', () => {
  it('shows the changed lines with context around them', () => {
    const text = 'a\nb\nc\nd\ne\nf\n'
    const s = pending(0, 6, 8, 'D\n') // d → D
    const ex = excerpt(text, s, 1)
    expect(ex.firstLine).toBe(3)
    expect(ex.before).toBe('c\nd\ne')
    expect(ex.after).toBe('c\nD\ne')
  })

  it('works for an insert at the end of the text', () => {
    const ex = excerpt('a\nb\n', pending(0, 4, 4, 'c\n'), 1)
    expect(ex.before).toBe('b')
    expect(ex.after).toBe('b\nc')
  })
})
