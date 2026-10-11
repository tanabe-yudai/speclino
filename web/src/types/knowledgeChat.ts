/**
 * 対話による知識の作成（S73）の型。src/speclino/domain/knowledge_chat.py に対応させる。
 * 会話は本人だけのもの。AI の返答は説明と下書きの提案で、提案は編集欄の本文のハッシュに結び付く。
 */

import type { ChatDraft, ProposalOutcome } from './chatDraft'
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
  /** 新規作成の会話だけ: 名前の案（規則に合うものだけ。名前の欄が空のときだけ入れる） */
  name?: string
}

export interface ChatProposal {
  base_hash: string
  raw_proposal: RawProposal
  diagnostics: ProposalDiagnostic[]
  valid: boolean
}

export type ChatSourceKind = 'knowledge' | 'repo' | 'web'

/** 返答の出典（サーバが、AI が実際に読んだもの・検索の出典から組み立てる）。 */
export interface ChatSource {
  kind: ChatSourceKind
  title: string
  ref: string
  url?: string | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  proposal?: ChatProposal | null
  idempotency_key?: string | null
  status: ChatMessageStatus
  created_at: string
  /** 調べた返答だけ: 出典 */
  sources?: ChatSource[] | null
  /** 調べた返答だけ: Google 検索の候補の表示（HTML。sandbox の iframe で出す） */
  search_suggestions?: string[] | null
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
  /** 提案の本文のここまでの全文（増分ではなく置き換える。表示専用） */
  | { type: 'draft'; field: string; text: string }
  /** AI の思考の要約の増えた分（足していく） */
  | { type: 'thought'; text: string }
  /** AI が今調べていること（人向けの文） */
  | { type: 'tool'; text: string }
  | { type: 'done'; turn: ChatTurn }
  | { type: 'error'; code: string; message: string; status: number }

/** 返答を待っている間の表示（送った発言と、届いた分の説明）。 */
export interface ChatStreaming {
  userText: string
  reply: string
  /** 思考の要約（ここまでの全文） */
  thought?: string
  /** 調べたこと（古い順。最後が今調べていること） */
  research?: string[]
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
  /**
   * 知識の編集画面（仕様・スキル・指示書）のとき true。提案の本文は編集欄に流し込み、
   * 会話には説明と 1 行の扱いだけを出す。規約の画面は従来どおり提案のカードを出す。
   */
  inline?: boolean
  /** 送った時点の本文の欄（生成中の差分の「前」） */
  bodyText?: string
  /** 提案ごとの扱い（発言の ID が鍵） */
  outcomes?: Record<string, ProposalOutcome>
  /**
   * 返答が確定したとき。`sentEditorText` は送った時点の編集欄の本文。Promise を返せば、
   * それが終わるまで生成中の表示を閉じない（反映と表示の差し替えを 1 回の描画にまとめる）
   */
  onReply?: (turn: ChatTurn, sentEditorText: string) => void | Promise<void>
}

export interface ChatSendOptions {
  /** 送った時点の本文の欄（途中経過の差分の「前」） */
  bodyText?: string
  /** 確定した返答を受けたとき。これ（Promise なら完了）を待ってから途中経過を閉じる（表示が一瞬戻らない） */
  onTurn?: (turn: ChatTurn) => void | Promise<void>
}

export interface KnowledgeChatController {
  chats: KnowledgeChatSummary[]
  current: KnowledgeChat | null
  sending: boolean
  /** 返答の途中経過（送信中だけ。無ければ null） */
  streaming: ChatStreaming | null
  /** 生成中の提案の本文（途中経過。送信中で、本文が届き始めてから） */
  draft: ChatDraft | null
  /** 返答を待つのをやめる（サーバへの接続を閉じる） */
  stop: () => void
  error: string | null
  listError: string | null
  select: (id: string) => void
  startNew: () => void
  /** 返答（または確定した結果）を得たら true。失敗したら本文を残して再送できるように false */
  send: (text: string, editorText: string, opts?: ChatSendOptions) => Promise<boolean>
  remove: (id: string) => Promise<void>
  summarize: () => Promise<ChatSummaryDraft | null>
  bind: (artifactId: string) => Promise<KnowledgeChat | null>
}
