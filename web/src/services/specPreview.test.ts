import { describe, expect, it } from 'vitest'
import { lineOffset, parseJsonField, sortDiagnostics } from './specPreview'
import type { SpecDiagnostic } from '@/types/knowledge'

const diag = (severity: 'error' | 'warning', line: number | null, code = 'E_X'): SpecDiagnostic => ({
  code,
  severity,
  message: code,
  span: line === null ? null : { diagram_id: 'd', line, column: 1 },
})

describe('sortDiagnostics', () => {
  it('puts errors first, then by line; diagnostics without a place go last in their group', () => {
    const sorted = sortDiagnostics([diag('warning', 1, 'W_A'), diag('error', null, 'E_B'), diag('error', 9, 'E_C'), diag('error', 2, 'E_D')])
    expect(sorted.map((d) => d.code)).toEqual(['E_D', 'E_C', 'E_B', 'W_A'])
  })
})

describe('lineOffset', () => {
  it('converts a 1-based line and column to a character offset', () => {
    const text = 'ab\ncde\nf'
    expect(lineOffset(text, 1, 1)).toBe(0)
    expect(lineOffset(text, 2, 2)).toBe(4)
    expect(lineOffset(text, 3, 1)).toBe(7)
  })

  it('clamps to the end of the text and the end of the line', () => {
    expect(lineOffset('ab\nc', 9, 1)).toBe(4)
    expect(lineOffset('ab\nc', 1, 99)).toBe(2)
  })
})

describe('parseJsonField', () => {
  it('reads empty text as the empty value', () => {
    expect(parseJsonField('  ', 'array')).toEqual({ value: [], error: null })
    expect(parseJsonField('', 'object')).toEqual({ value: null, error: null })
  })

  it('reports invalid JSON and the wrong shape', () => {
    expect(parseJsonField('{', 'object').error).toBe('JSON として読めません')
    expect(parseJsonField('{}', 'array').error).toBe('JSON の配列にしてください')
    expect(parseJsonField('[]', 'object').error).toBe('JSON のオブジェクトにしてください')
    expect(parseJsonField('[{"a":1}]', 'array')).toEqual({ value: [{ a: 1 }], error: null })
  })
})
