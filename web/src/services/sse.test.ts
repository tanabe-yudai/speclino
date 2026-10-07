import { describe, expect, it } from 'vitest'
import { createSseParser } from './sse'

function run(chunks: string[]) {
  const p = createSseParser()
  return [...chunks.flatMap((c) => p.push(c)), ...p.flush()]
}

describe('sse parser', () => {
  it('空行でイベントを確定し、event と data を読む', () => {
    expect(run(['event: delta\ndata: {"text":"あ"}\n\nevent: done\ndata: {}\n\n'])).toEqual([
      { event: 'delta', data: '{"text":"あ"}' },
      { event: 'done', data: '{}' },
    ])
  })

  it('空行が来るまでは確定しない（断片に分かれても同じ結果）', () => {
    const raw = 'event: delta\ndata: x\n\nevent: done\ndata: y\n\n'
    const expected = run([raw])
    for (const size of [1, 2, 3, 7]) {
      const chunks = raw.match(new RegExp(`[\\s\\S]{1,${size}}`, 'g')) ?? []
      expect(run(chunks)).toEqual(expected)
    }
    const p = createSseParser()
    expect(p.push('event: delta\ndata: x\n')).toEqual([])
    expect(p.push('\n')).toEqual([{ event: 'delta', data: 'x' }])
  })

  it('data が複数行なら \\n でつなぐ', () => {
    expect(run(['data: a\ndata: b\ndata:\ndata: c\n\n'])).toEqual([{ event: 'message', data: 'a\nb\n\nc' }])
  })

  it('コメント行（生存確認）は無視する', () => {
    expect(run([': ping\n\n: ping\n\nevent: x\n: inline\ndata: 1\n\n'])).toEqual([{ event: 'x', data: '1' }])
  })

  it('CRLF と CR を受け付け、CRLF が断片の間で割れても 1 つの改行', () => {
    expect(run(['event: a\r\ndata: 1\r\n\r\n'])).toEqual([{ event: 'a', data: '1' }])
    expect(run(['event: a\rdata: 1\r\r'])).toEqual([{ event: 'a', data: '1' }])
    expect(run(['event: a\r', '\ndata: 1\r', '\n\r', '\n'])).toEqual([{ event: 'a', data: '1' }])
  })

  it('入力の終わりで、閉じられなかった残りも確定する', () => {
    expect(run(['event: done\ndata: {"a":1}'])).toEqual([{ event: 'done', data: '{"a":1}' }])
    expect(run(['event: done\ndata: z\n'])).toEqual([{ event: 'done', data: 'z' }])
    expect(run(['event: only\n'])).toEqual([])
  })

  it('data の先頭の空白は 1 つだけ外す', () => {
    expect(run(['data:  two\n\n', 'data:none\n\n'])).toEqual([
      { event: 'message', data: ' two' },
      { event: 'message', data: 'none' },
    ])
  })
})
