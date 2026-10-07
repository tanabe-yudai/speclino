import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChatStreamEvent, ChatTurn } from '@/types/knowledgeChat'
import { postMessageStream } from './knowledgeChat'
import { ApiError, StreamInterruptedError } from './errors'

vi.mock('../auth/authHeaders', () => ({ getAuthHeaders: async () => ({ 'X-Dev-User': 'judge' }) }))

const body = { text: 't', idempotency_key: 'key-0001', editor_text: '' }
const turn = { chat: { id: 'c' }, user_message_id: 'u', reply: null, replayed: false } as unknown as ChatTurn

function sse(parts: string[]): Response {
  const enc = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const p of parts) c.enqueue(enc.encode(p))
      c.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

async function collect(res: Response | (() => Response)) {
  const fetchMock = vi.fn(async () => (typeof res === 'function' ? res() : res))
  vi.stubGlobal('fetch', fetchMock)
  const events: ChatStreamEvent[] = []
  const result = await postMessageStream('w', 'c', body, (e) => events.push(e)).then(
    () => null,
    (e: unknown) => e,
  )
  return { events, result, fetchMock }
}

afterEach(() => vi.unstubAllGlobals())

describe('postMessageStream', () => {
  it('delta を順に渡し、done で終わる（マルチバイトが断片の間で割れても読める）', async () => {
    const raw = `event: status\ndata: {"state":"thinking"}\n\n: ping\n\nevent: delta\ndata: {"text":"あい"}\n\nevent: done\ndata: ${JSON.stringify(turn)}\n\n`
    const bytes = new TextEncoder().encode(raw)
    const enc = (a: number, b: number) => bytes.slice(a, b)
    const cut = raw.indexOf('あ') // 文字の途中で切る
    const byteCut = new TextEncoder().encode(raw.slice(0, cut)).length + 1
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc(0, byteCut))
        c.enqueue(enc(byteCut, bytes.length))
        c.close()
      },
    })
    const { events, result, fetchMock } = await collect(new Response(stream))
    expect(result).toBeNull()
    expect(events.map((e) => e.type)).toEqual(['status', 'delta', 'done'])
    expect(events[1]).toEqual({ type: 'delta', text: 'あい' })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/workspaces/w/knowledge/chats/c/messages/stream')
    expect(init.headers).toMatchObject({ 'X-Dev-User': 'judge', Accept: 'text/event-stream' })
  })

  it('error イベントも終わりとして渡す', async () => {
    const { events, result } = await collect(sse(['event: error\ndata: {"code":"cost_limit","message":"上限","status":429}\n\n']))
    expect(result).toBeNull()
    expect(events).toEqual([{ type: 'error', code: 'cost_limit', message: '上限', status: 429 }])
  })

  it('done も error も無く終わったら途中で切れたエラー', async () => {
    const { result } = await collect(sse(['event: delta\ndata: {"text":"a"}\n\n']))
    expect(result).toBeInstanceOf(StreamInterruptedError)
    expect((result as Error).message).toBe('応答が途中で切れました。もう一度送ってください')
  })

  it('最後の空行が無くても done を確定する', async () => {
    const { events, result } = await collect(sse([`event: done\ndata: ${JSON.stringify(turn)}`]))
    expect(result).toBeNull()
    expect(events.map((e) => e.type)).toEqual(['done'])
  })

  it('ストリームの前の失敗は ApiError（コードつき）', async () => {
    const res = new Response(JSON.stringify({ error: { code: 'chat_unavailable', message: '使えません' } }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    })
    const { result } = await collect(res)
    expect(result).toBeInstanceOf(ApiError)
    expect(result).toMatchObject({ status: 503, code: 'chat_unavailable' })
  })

  it('ルートが無い（コード無しの 404 / 405）なら非ストリームにする', async () => {
    let calls = 0
    const { events, result, fetchMock } = await collect(() => {
      calls += 1
      if (calls === 1) return new Response('{"detail":"Not Found"}', { status: 404, headers: { 'Content-Type': 'application/json' } })
      return new Response(JSON.stringify(turn), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    expect(result).toBeNull()
    expect(events).toEqual([{ type: 'done', turn }])
    expect((fetchMock.mock.calls as unknown as string[][])[1][0]).toBe('/api/workspaces/w/knowledge/chats/c/messages')
  })

  it('コードつきの 404（会話が無い）は非ストリームにせずそのまま失敗', async () => {
    const res = new Response(JSON.stringify({ error: { code: 'workspace_not_found', message: '無い' } }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
    const { result, fetchMock } = await collect(res)
    expect(result).toMatchObject({ status: 404, code: 'workspace_not_found' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
