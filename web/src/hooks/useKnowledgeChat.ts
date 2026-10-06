import { useCallback, useEffect, useRef, useState } from 'react'
import { createChat, deleteChat, getChat, listChats, patchChat, postMessage, summarizeChat } from '@/api/knowledgeChat'
import { chatsFor, describeChatError, newIdempotencyKey } from '@/services/knowledgeChat'
import type { ChatSummaryDraft, KnowledgeChat, KnowledgeChatController, KnowledgeChatKind, KnowledgeChatSummary } from '@/types/knowledgeChat'

/**
 * 自分の会話の一覧と、開いている会話（S73）。会話は本人だけのもの。
 * 新しい会話は最初の発言のときに作る（何も送らずに閉じた会話を残さない）。
 * artifactId が null なら、まだ結び付いていない会話（新規作成中・規約）を扱う。
 */
export function useKnowledgeChat(
  slug: string,
  kind: KnowledgeChatKind,
  artifactId: string | null,
  initialChatId: string | null,
): KnowledgeChatController {
  const [chats, setChats] = useState<KnowledgeChatSummary[]>([])
  const [current, setCurrent] = useState<KnowledgeChat | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const refresh = useCallback(() => {
    // 規約の会話は artifact に結び付かない。知識は kind で取り、編集中のものに絞る
    listChats(slug, kind, artifactId)
      .then((rows) => {
        if (!alive.current) return
        setChats(kind === 'policy' ? rows : chatsFor(rows, artifactId))
        setListError(null)
      })
      .catch((err: unknown) => alive.current && setListError(describeChatError(err)))
  }, [slug, kind, artifactId])

  useEffect(refresh, [refresh])

  const load = useCallback(
    (id: string) => {
      getChat(slug, id)
        .then((chat) => {
          if (!alive.current) return
          setCurrent(chat)
          setError(null)
        })
        .catch((err: unknown) => alive.current && setError(describeChatError(err)))
    },
    [slug],
  )

  const select = useCallback(
    (id: string) => {
      setError(null)
      load(id)
    },
    [load],
  )

  useEffect(() => {
    if (initialChatId) load(initialChatId)
  }, [initialChatId, load])

  const startNew = useCallback(() => {
    setError(null)
    setCurrent(null)
  }, [])

  async function send(text: string, editorText: string) {
    setSending(true)
    setError(null)
    try {
      const chat = current ?? (await createChat(slug, kind, artifactId))
      if (!current && alive.current) setCurrent(chat)
      const turn = await postMessage(slug, chat.id, { text, idempotency_key: newIdempotencyKey(), editor_text: editorText })
      if (alive.current) setCurrent(turn.chat)
    } catch (err) {
      if (!alive.current) return
      setError(describeChatError(err))
      // 失敗した発言も会話に残るので、読み直して表示を揃える
      const id = current?.id
      if (id) getChat(slug, id).then((c) => alive.current && setCurrent(c)).catch(() => undefined)
    } finally {
      if (alive.current) setSending(false)
      refresh()
    }
  }

  async function remove(id: string) {
    setError(null)
    try {
      await deleteChat(slug, id)
      if (current?.id === id) setCurrent(null)
    } catch (err) {
      setError(describeChatError(err))
    } finally {
      refresh()
    }
  }

  async function summarize(): Promise<ChatSummaryDraft | null> {
    if (!current) return null
    setError(null)
    try {
      return await summarizeChat(slug, current.id)
    } catch (err) {
      setError(describeChatError(err))
      return null
    }
  }

  /** 新規作成の会話を、最初の保存でできた知識に結び付ける。 */
  async function bind(target: string): Promise<KnowledgeChat | null> {
    if (!current || current.artifact_id === target) return current
    try {
      const chat = await patchChat(slug, current.id, current.version, { artifact_id: target })
      if (alive.current) setCurrent(chat)
      return chat
    } catch (err) {
      setError(describeChatError(err))
      return null
    }
  }

  return { chats, current, sending, error, listError, select, startNew, send, remove, summarize, bind }
}
