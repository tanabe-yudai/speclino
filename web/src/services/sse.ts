/**
 * Server-Sent Events のパーサ（純粋関数。fetch の ReadableStream から読んだ文字列を渡す）。
 * 行の区切りは LF / CRLF / CR。空行でイベントを確定し、`data:` の複数行は `\n` でつなぐ。
 * `:` で始まる行（生存確認のコメント）は無視する。
 */

import type { SseMessage } from '@/types/sse'

export interface SseParser {
  /** 受け取った文字列を足し、確定したイベントを返す（行の途中は次の push まで持ち越す） */
  push: (chunk: string) => SseMessage[]
  /** 入力の終わり。残りの行と、空行で閉じられなかったイベントも確定して返す */
  flush: () => SseMessage[]
}

export function createSseParser(): SseParser {
  let buffer = ''
  let event = ''
  let data: string[] = []
  let hasData = false

  const dispatch = (out: SseMessage[]) => {
    if (hasData) out.push({ event: event || 'message', data: data.join('\n') })
    event = ''
    data = []
    hasData = false
  }

  const line = (text: string, out: SseMessage[]) => {
    if (text === '') return dispatch(out)
    if (text.startsWith(':')) return
    const i = text.indexOf(':')
    const field = i < 0 ? text : text.slice(0, i)
    let value = i < 0 ? '' : text.slice(i + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') {
      data.push(value)
      hasData = true
    }
  }

  const drain = (out: SseMessage[], final: boolean) => {
    let start = 0
    for (let i = 0; i < buffer.length; i++) {
      const c = buffer[i]
      if (c !== '\n' && c !== '\r') continue
      // 末尾の CR は、次の断片の先頭が LF かもしれないので持ち越す
      if (c === '\r' && i === buffer.length - 1 && !final) break
      line(buffer.slice(start, i), out)
      if (c === '\r' && buffer[i + 1] === '\n') i++
      start = i + 1
    }
    buffer = buffer.slice(start)
  }

  return {
    push(chunk) {
      const out: SseMessage[] = []
      buffer += chunk
      drain(out, false)
      return out
    },
    flush() {
      const out: SseMessage[] = []
      drain(out, true)
      if (buffer !== '') {
        line(buffer, out)
        buffer = ''
      }
      dispatch(out)
      return out
    },
  }
}
