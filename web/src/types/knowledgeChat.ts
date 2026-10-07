/**
 * 対話による知識の作成（S73）の型。src/speclino/domain/knowledge_chat.py に対応させる。
 * 会話は本人だけのもの。AI の返答は説明と下書きの提案で、提案は編集欄の本文のハッシュに結び付く。
 */

import type { PolicyRule } from './policy'

export type KnowledgeChatKind = 'spec' | 'skill' | 'instructions' | 'policy'
export type ChatMessageStatus = 'pending' | 'done' | 'failed'
export type NoteSource = 'manual' | 'chat_summary'

export interface ProposalDiagnostic {
  severity: 'error' | 'warning' | 'info'
  code: string
  message: string
}

/** 規約の提案のルール（許可リストの欄だけ）。 */
export type ProposedRule = Partial<Pick<PolicyRule, 'id' | 'title' | 'text' | 'level' | 'verifier' | 'applies_to'>> & {
  tier?: 'deterministic' | 'llm_assisted'
}

/** 種類ごとの許可リストの欄だけ。型に合わない値も入りうる（診断を見る）。 */
export interface RawProposal {
  markdown?: string
  skill_md?: string
  files?: Record<string, string>
  body?: string
  rule?: ProposedRule
}

export interface ChatProposal {
  base_hash: string
  raw_proposal: RawProposal
  diagnostics: ProposalDiagnostic[]
  valid: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  proposal?: ChatProposal | null
  idempotency_key?: string | null
  status: ChatMessageStatus
  created_at: string
}

export interface KnowledgeChat {
  id: string
  workspace: string
  owner_uid: string
  kind: KnowledgeChatKind
  artifact_id: string | null
  title: string
  messages: ChatMessage[]
  version: number
  created_at: string
  updated_at: string
}

export interface KnowledgeChatSummary {
  id: string
  kind: KnowledgeChatKind
  artifact_id: string | null
  title: string
  message_count: number
  version: number
  updated_at: string
}

export interface ChatTurn {
  chat: KnowledgeChat
  user_message_id: string
  reply: ChatMessage | null
  replayed: boolean
}

/** ストリームで届く出来事。`delta` は説明の増えた分、`done` は非ストリームと同じ ChatTurn。 */
export type ChatStreamEvent =
  | { type: 'status'; state: string }
  | { type: 'delta'; text: string }
  | { type: 'done'; turn: ChatTurn }
  | { type: 'error'; code: string; message: string; status: number }

/** 返答を待っている間の表示（送った発言と、届いた分の説明）。 */
export interface ChatStreaming {
  userText: string
  reply: string
}

export interface ChatSummaryDraft {
  what: string
  why: string
  dropped: string
  note: string
}

/** 改訂の説明の欄の状態。source は本人の申告（要約から入れたか）。 */
export interface NoteState {
  text: string
  source: NoteSource
}

export interface ChatPanelProps {
  chat: KnowledgeChatController
  /** 今の編集欄の本文（提案の土台。サーバに送り、ハッシュで古さを見る） */
  editorText: string
  /** 差分の「前」: 提案が置き換える今の欄（規約は、提案の ID の既存のルールのこともある） */
  beforeText: (proposal: RawProposal) => string
  /** 差分の「後」: 提案を重ねた欄（skill の files が無ければ今の付属ファイルのまま） */
  afterText: (proposal: RawProposal) => string
  onApply: (proposal: RawProposal) => void
}

export interface KnowledgeChatController {
  chats: KnowledgeChatSummary[]
  current: KnowledgeChat | null
  sending: boolean
  /** 返答の途中経過（送信中だけ。無ければ null） */
  streaming: ChatStreaming | null
  /** 返答を待つのをやめる（サーバへの接続を閉じる） */
  stop: () => void
  error: string | null
  listError: string | null
  select: (id: string) => void
  startNew: () => void
  /** 返答（または確定した結果）を得たら true。失敗したら本文を残して再送できるように false */
  send: (text: string, editorText: string) => Promise<boolean>
  remove: (id: string) => Promise<void>
  summarize: () => Promise<ChatSummaryDraft | null>
  bind: (artifactId: string) => Promise<KnowledgeChat | null>
}
