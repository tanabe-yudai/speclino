/** 対話による知識の作成（S73）のエンドポイント関数。判断は持たない。 */

import { apiFetch } from './client'
import type {
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
): Promise<ChatTurn> {
  return apiFetch<ChatTurn>(`${one(slug, id)}/messages`, { method: 'POST', headers: json, body: JSON.stringify(body) })
}

export function summarizeChat(slug: string, id: string): Promise<ChatSummaryDraft> {
  return apiFetch<ChatSummaryDraft>(`${one(slug, id)}/summary`, { method: 'POST' })
}
