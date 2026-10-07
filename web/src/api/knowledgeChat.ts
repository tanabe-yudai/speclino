/** 対話による知識の作成（S73）のエンドポイント関数。判断は持たない。 */

import { getAuthHeaders } from '../auth/authHeaders'
import { apiFetch, readError } from './client'
import { ApiError, StreamInterruptedError } from './errors'
import { parseChatStreamEvent } from '../services/knowledgeChat'
import { createSseParser } from '../services/sse'
import type {
  ChatStreamEvent,
  ChatSummaryDraft,
  ChatTurn,
  KnowledgeChat,
  KnowledgeChatKind,
  KnowledgeChatSummary,
} from '../types/knowledgeChat'

const base = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}/knowledge/chats`
const one = (slug: string, id: string) => `${base(slug)}/${encodeURIComponent(id)}`
const json = { 'Content-Type': 'application/json' }

export function listChats(slug: string, kind: KnowledgeChatKind, artifactId?: string | null): Promise<KnowledgeChatSummary[]> {
  const q = new URLSearchParams({ kind })
  if (artifactId) q.set('artifact_id', artifactId)
  return apiFetch<KnowledgeChatSummary[]>(`${base(slug)}?${q.toString()}`)
}

export function createChat(slug: string, kind: KnowledgeChatKind, artifactId: string | null): Promise<KnowledgeChat> {
  return apiFetch<KnowledgeChat>(base(slug), {
    method: 'POST',
    headers: json,
    body: JSON.stringify(artifactId ? { kind, artifact_id: artifactId } : { kind }),
  })
}

export function getChat(slug: string, id: string): Promise<KnowledgeChat> {
  return apiFetch<KnowledgeChat>(one(slug, id))
}

export function deleteChat(slug: string, id: string): Promise<void> {
  return apiFetch<void>(one(slug, id), { method: 'DELETE' })
}

export function patchChat(
  slug: string,
  id: string,
  expectedVersion: number,
  patch: { title?: string; artifact_id?: string },
): Promise<KnowledgeChat> {
  return apiFetch<KnowledgeChat>(one(slug, id), {
    method: 'PATCH',
    headers: json,
    body: JSON.stringify({ expected_version: expectedVersion, ...patch }),
  })
}

export function postMessage(
  slug: string,
  id: string,
  body: { text: string; idempotency_key: string; editor_text: string },
  signal?: AbortSignal,
): Promise<ChatTurn> {
  return apiFetch<ChatTurn>(`${one(slug, id)}/messages`, { method: 'POST', headers: json, body: JSON.stringify(body), signal })
}

/**
 * postMessage のストリーム版（SSE）。出来事を onEvent に渡し、`done` か `error` を受けたら終わる。
 * どちらも来ずに閉じたら StreamInterruptedError。ストリームの API が無いサーバ（404/405）では
 * 非ストリームの postMessage にして、`done` だけを渡す。signal で中断できる（AbortError）。
 */
export async function postMessageStream(
  slug: string,
  id: string,
  body: { text: string; idempotency_key: string; editor_text: string },
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const authHeaders = await getAuthHeaders()
  const response = await fetch(`${one(slug, id)}/messages/stream`, {
    method: 'POST',
    headers: { ...authHeaders, ...json, Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) {
    const { message, code } = await readError(response)
    // ルート自体が無い（古いサーバ）。ワークスペース無しなどの 404 は code が付くので区別できる
    if (response.status === 405 || (response.status === 404 && code === null)) {
      onEvent({ type: 'done', turn: await postMessage(slug, id, body, signal) })
      return
    }
    throw new ApiError(response.status, message, code)
  }
  if (!response.body) throw new StreamInterruptedError()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = createSseParser()
  let finished = false
  const handle = (messages: ReturnType<typeof parser.push>) => {
    for (const msg of messages) {
      const event = parseChatStreamEvent(msg)
      if (!event) continue
      if (event.type === 'done' || event.type === 'error') finished = true
      onEvent(event)
    }
  }
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      handle(parser.push(decoder.decode(value, { stream: true })))
    }
    handle(parser.push(decoder.decode()))
    handle(parser.flush())
  } finally {
    reader.cancel().catch(() => undefined)
  }
  if (!finished) throw new StreamInterruptedError()
}

export function summarizeChat(slug: string, id: string): Promise<ChatSummaryDraft> {
  return apiFetch<ChatSummaryDraft>(`${one(slug, id)}/summary`, { method: 'POST' })
}
