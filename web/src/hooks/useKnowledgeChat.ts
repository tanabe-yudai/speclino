import { useCallback, useEffect, useRef, useState } from 'react'
import { createChat, deleteChat, getChat, listChats, patchChat, postMessageStream, summarizeChat } from '@/api/knowledgeChat'
import { ApiError, StreamInterruptedError } from '@/api/errors'
import { chatsFor, describeChatError, idempotencyKeyFor } from '@/services/knowledgeChat'
import type { ChatDraft } from '@/types/chatDraft'
import type { ChatSendOptions, ChatStreaming, ChatSummaryDraft, ChatTurn, KnowledgeChat, KnowledgeChatController, KnowledgeChatKind, KnowledgeChatSummary } from '@/types/knowledgeChat'

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
  const [streaming, setStreaming] = useState<ChatStreaming | null>(null)
  const [draft, setDraft] = useState<ChatDraft | null>(null)
  const abort = useRef<AbortController | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  // 失敗した発言（届いたか分からない）。同じ本文の再送は同じキーで送り、二重にしない
  const retry = useRef<{ text: string; key: string } | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      abort.current?.abort()
    }
  }, [])

  const stop = useCallback(() => abort.current?.abort(), [])

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

  async function send(text: string, editorText: string, opts: ChatSendOptions = {}): Promise<boolean> {
    setSending(true)
    setError(null)
    setDraft(null)
    setStreaming({ userText: text, reply: '' })
    const key = idempotencyKeyFor(text, retry.current)
    retry.current = { text, key }
    const controller = new AbortController()
    abort.current = controller
    try {
      const chat = current ?? (await createChat(slug, kind, artifactId))
      if (!current && alive.current) setCurrent(chat)
      let turn: ChatTurn | null = null
      await postMessageStream(
        slug,
        chat.id,
        { text, idempotency_key: key, editor_text: editorText },
        (event) => {
          if (!alive.current) return
          if (event.type === 'delta') setStreaming((s) => (s ? { ...s, reply: s.reply + event.text } : s))
          else if (event.type === 'draft') setDraft({ field: event.field, before: opts.bodyText ?? '', text: event.text })
          else if (event.type === 'done') turn = event.turn
          else if (event.type === 'error') throw new ApiError(event.status, event.message, event.code)
        },
        controller.signal,
      )
      if (!turn) throw new StreamInterruptedError()
      const done: ChatTurn = turn
      // 結果が確定した（返答あり、または前の失敗の再送）。次の発言は新しいキーで送る
      retry.current = null
      if (alive.current) {
        setCurrent(done.chat)
        // 途中経過を閉じる前に、確定した提案を反映させる（同じ描画にまとまり、表示が一瞬戻らない）
        if (!done.replayed || done.reply) opts.onTurn?.(done)
      }
      if (done.replayed && !done.reply && alive.current) setError('この発言は前に失敗しています。もう一度送ると新しい発言として送ります')
      return !done.replayed || !!done.reply
    } catch (err) {
      if (!alive.current) return false
      const stopped = controller.signal.aborted
      // 止めた発言はサーバ側で失敗になる。次は新しい発言として送る
      if (stopped) retry.current = null
      setError(stopped ? '返答を待つのをやめました。もう一度送れます' : describeChatError(err))
      // 失敗した発言も会話に残るので、読み直して表示を揃える
      const id = current?.id
      if (id) getChat(slug, id).then((c) => alive.current && setCurrent(c)).catch(() => undefined)
      return false
    } finally {
      abort.current = null
      if (alive.current) {
        setSending(false)
        setStreaming(null)
        setDraft(null)
      }
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
      let chat: KnowledgeChat
      try {
        chat = await patchChat(slug, current.id, current.version, { artifact_id: target })
      } catch (err) {
        // 別の画面で版が進んでいたら、読み直して一度だけやり直す
        if (!(err instanceof ApiError && err.code === 'version_conflict')) throw err
        const fresh = await getChat(slug, current.id)
        chat = await patchChat(slug, fresh.id, fresh.version, { artifact_id: target })
      }
      if (alive.current) setCurrent(chat)
      return chat
    } catch (err) {
      setError(describeChatError(err))
      return null
    }
  }

  return { chats, current, sending, streaming, draft, stop, error, listError, select, startNew, send, remove, summarize, bind }
}
