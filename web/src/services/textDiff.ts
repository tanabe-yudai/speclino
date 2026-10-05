/** 行単位の差分（LCS）。改訂の差分の表示に使う。 */

export interface DiffLine {
  op: 'same' | 'add' | 'del'
  text: string
  oldNo: number | null
  newNo: number | null
}

/** 既定の上限: 共通の先頭・末尾を除いた中央部の 行数×行数。超えたら null（間違った差分を出さない）。 */
export const DIFF_MAX_CELLS = 4_000_000

const split = (s: string) => (s === '' ? [] : s.split('\n'))

export function diffLines(before: string, after: string, maxCells = DIFF_MAX_CELLS): DiffLine[] | null {
  const a = split(before)
  const b = split(after)
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  const midA = a.slice(head, a.length - tail)
  const midB = b.slice(head, b.length - tail)
  if (midA.length * midB.length > maxCells) return null

  const out: DiffLine[] = []
  for (let i = 0; i < head; i++) out.push({ op: 'same', text: a[i], oldNo: i + 1, newNo: i + 1 })

  // lcs[i][j] = midA[i..] と midB[j..] の LCS の長さ
  const n = midA.length
  const m = midB.length
  const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = midA[i] === midB[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }
  let i = 0
  let j = 0
  while (i < n || j < m) {
    if (i < n && j < m && midA[i] === midB[j]) {
      out.push({ op: 'same', text: midA[i], oldNo: head + i + 1, newNo: head + j + 1 })
      i++
      j++
    } else if (j >= m || (i < n && lcs[i + 1][j] >= lcs[i][j + 1])) {
      out.push({ op: 'del', text: midA[i], oldNo: head + i + 1, newNo: null })
      i++
    } else {
      out.push({ op: 'add', text: midB[j], oldNo: null, newNo: head + j + 1 })
      j++
    }
  }
  for (let k = 0; k < tail; k++) {
    const oi = a.length - tail + k
    const ni = b.length - tail + k
    out.push({ op: 'same', text: a[oi], oldNo: oi + 1, newNo: ni + 1 })
  }
  return out
}
