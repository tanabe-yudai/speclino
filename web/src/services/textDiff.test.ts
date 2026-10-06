import { describe, expect, it } from 'vitest'
import { diffLines } from './textDiff'

describe('diffLines', () => {
  it('marks added, removed and unchanged lines with line numbers', () => {
    const d = diffLines('a\nb\nc', 'a\nx\nc')
    expect(d).toEqual([
      { op: 'same', text: 'a', oldNo: 1, newNo: 1 },
      { op: 'del', text: 'b', oldNo: 2, newNo: null },
      { op: 'add', text: 'x', oldNo: null, newNo: 2 },
      { op: 'same', text: 'c', oldNo: 3, newNo: 3 },
    ])
  })

  it('treats an empty side as all added / all removed', () => {
    expect(diffLines('', 'a\nb')?.map((l) => l.op)).toEqual(['add', 'add'])
    expect(diffLines('a', '')?.map((l) => l.op)).toEqual(['del'])
    expect(diffLines('', '')).toEqual([])
  })

  it('returns null instead of a wrong diff when the input is too large', () => {
    const a = Array.from({ length: 300 }, (_, i) => `a${i}`).join('\n')
    const b = Array.from({ length: 300 }, (_, i) => `b${i}`).join('\n')
    expect(diffLines(a, b, 1000)).toBeNull()
    // 共通の先頭・末尾は上限に数えない
    const same = Array.from({ length: 5000 }, (_, i) => `s${i}`).join('\n')
    expect(diffLines(same + '\nx', same + '\ny', 1000)?.filter((l) => l.op !== 'same')).toHaveLength(2)
  })
})
