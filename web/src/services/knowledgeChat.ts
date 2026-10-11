/**
 * 対話による知識の作成（S73）の画面の判断。表示はコンポーネント、通信は api/knowledgeChat.ts。
 *
 * 提案は「編集欄の本文」（種類ごとの文字列）のハッシュに結び付く。画面は送ったときと同じ直列化で
 * 今の本文のハッシュを計算し、違えば古い提案として「エディタに反映」を止める。反映は編集欄に
 * 入れるだけで、保存と承認は人が行う。
 */

import { ApiError, StreamInterruptedError } from '@/api/errors'
import type { ArtifactContent } from '@/types/knowledge'
import type { SseMessage } from '@/types/sse'
import type {
  ChatProposal,
  ChatStreamEvent,
  ChatStreaming,
  ChatSummaryDraft,
  ChatTurn,
  KnowledgeChat,
  KnowledgeChatSummary,
  NoteState,
  RawProposal,
} from '@/types/knowledgeChat'
import type { PolicyRule } from '@/types/policy'

/** パネルの最初に出す告知（設計 5 章、文言は固定）。 */
export const CHAT_NOTICE = '会話は Google の Gemini に送られ、SpecLino の運用者が読む場合があります。ほかのメンバーには見えません'

export const NOTE_MAX_CHARS = 500
/** 1 回の発言の上限（UTF-8 のバイト数。サーバの MESSAGE_MAX_BYTES と同じ） */
export const MESSAGE_MAX_BYTES = 8192

export function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length
}

/** 発言の残りのバイト数（負なら超えている）。 */
export function messageBytesLeft(text: string): number {
  return MESSAGE_MAX_BYTES - utf8Bytes(text)
}

/** 失敗した発言の再送: 同じ本文なら同じ冪等キー（届いていた発言を二重にしない）。 */
export function idempotencyKeyFor(text: string, retry: { text: string; key: string } | null): string {
  return retry && retry.text === text ? retry.key : newIdempotencyKey()
}

/** キーの順に依らない JSON（提案の土台の直列化。送る文字列とハッシュを取る文字列を同じにする）。 */
export function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(',')}]`
  if (v && typeof v === 'object') {
    const entries = Object.entries(v as Record<string, unknown>)
      .filter(([, val]) => val !== undefined)
      .sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0))
    return `{${entries.map(([k, val]) => `${JSON.stringify(k)}:${canonicalJson(val)}`).join(',')}}`
  }
  return JSON.stringify(v ?? null)
}

/** 知識の編集欄の本文（AI に見せ、提案を結び付ける文字列）。名前・スコープ・対象は含めない。 */
export function editorText(content: ArtifactContent): string {
  switch (content.kind) {
    case 'spec':
      return content.markdown
    case 'instructions':
      return content.body
    case 'skill':
      return canonicalJson({ skill_md: content.skill_md, files: content.files })
  }
}

/** 規約の編集中のルール（無ければ空のオブジェクト）。 */
export function ruleEditorText(rule: PolicyRule | null): string {
  return canonicalJson(rule ?? {})
}

/** 提案の土台と同じ形のハッシュ（`sha256:` + 16 進）。 */
export async function textHash(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return `sha256:${Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')}`
}

/** 冪等キー（同じ発言の再送は同じキー）。 */
export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}

function filesText(files: Record<string, string>): string {
  return Object.keys(files)
    .sort()
    .map((path) => `=== ${path}\n${files[path]}`)
    .join('\n\n')
}

function ruleText(rule: unknown): string {
  return JSON.stringify(rule ?? {}, null, 2)
}

/** 差分の「前」: 今の編集欄のうち、提案が置き換える欄。 */
export function currentFieldText(content: ArtifactContent): string {
  switch (content.kind) {
    case 'spec':
      return content.markdown
    case 'instructions':
      return content.body
    case 'skill':
      return [`=== SKILL.md\n${content.skill_md}`, filesText(content.files)].filter(Boolean).join('\n\n')
  }
}

const RULE_FIELDS = ['id', 'title', 'text', 'level', 'tier', 'verifier', 'applies_to'] as const

/** 規約の差分の「前」: 編集中のルールのうち、提案できる欄。 */
export function currentRuleText(rule: PolicyRule | null): string {
  if (!rule) return ''
  const picked: Record<string, unknown> = {}
  for (const k of RULE_FIELDS) if (k in rule) picked[k] = (rule as unknown as Record<string, unknown>)[k]
  return ruleText(picked)
}

/** 差分の「後」: 提案の欄。skill の files が無ければ今の付属ファイルを変えない。 */
export function proposalText(raw: RawProposal, current?: ArtifactContent): string {
  if (raw.rule) return ruleText(raw.rule)
  if (raw.markdown !== undefined) return raw.markdown
  if (raw.body !== undefined) return raw.body
  if (raw.skill_md !== undefined) {
    const files = raw.files ?? (current?.kind === 'skill' ? current.files : {})
    return [`=== SKILL.md\n${raw.skill_md}`, filesText(files)].filter(Boolean).join('\n\n')
  }
  return ''
}

/** 提案を編集欄の内容に重ねる（名前・スコープ・対象は変えない）。 */
export function applyProposal(content: ArtifactContent, raw: RawProposal): ArtifactContent {
  switch (content.kind) {
    case 'spec':
      return typeof raw.markdown === 'string' ? { ...content, markdown: raw.markdown } : content
    case 'instructions':
      return typeof raw.body === 'string' ? { ...content, body: raw.body } : content
    case 'skill':
      return typeof raw.skill_md === 'string'
        ? { ...content, skill_md: raw.skill_md, files: raw.files ?? content.files }
        : content
  }
}

/** 新規作成で名前の欄が空のときだけ、提案の名前の案を使う（S79）。それ以外は今の名前のまま。 */
export function proposedName(current: string, raw: RawProposal, creating: boolean): string {
  return creating && !current && raw.name ? raw.name : current
}

/**
 * 確定した返答を出す順（S79）: 提案の反映（非同期のことがある）を待ってから会話を差し替え、
 * 途中経過を閉じる。旧本文の編集欄が一瞬出るなど、ページの高さが 2 回変わるのを避ける。
 */
export async function settleTurn(turn: ChatTurn, onTurn: ((turn: ChatTurn) => void | Promise<void>) | undefined, show: () => void): Promise<void> {
  await onTurn?.(turn)
  show()
}

/** 規約の提案をルールに重ねる。版・対象が無いときの扱い・例外の可否は今の値を残す。 */
export function applyRuleProposal(rule: PolicyRule, raw: RawProposal): PolicyRule {
  const p = raw.rule
  if (!p) return rule
  return {
    ...rule,
    ...(p.id !== undefined && { id: p.id }),
    ...(p.title !== undefined && { title: p.title }),
    ...(p.text !== undefined && { text: p.text }),
    ...(p.level !== undefined && { level: p.level }),
    ...(p.tier !== undefined && { tier: p.tier }),
    ...(p.verifier !== undefined && { verifier: p.verifier }),
    ...(p.applies_to !== undefined && { applies_to: { ...rule.applies_to, ...p.applies_to } }),
  }
}

export function errorCount(proposal: ChatProposal): number {
  return proposal.diagnostics.filter((d) => d.severity === 'error').length
}

/** 要約できるか（AI が答えた発言が 1 つ以上ある）。 */
export function canSummarize(chat: KnowledgeChat | null): boolean {
  return !!chat && chat.messages.some((m) => m.role === 'user' && m.status === 'done')
}

/** 要約の案を改訂の説明の欄に入れる（本人が直してから保存する）。 */
export function noteFromSummary(draft: ChatSummaryDraft): NoteState {
  return { text: draft.note.slice(0, NOTE_MAX_CHARS), source: 'chat_summary' }
}

/** 説明の欄を手で変えたとき。空にしたら手書き扱いに戻す。 */
export function editNote(prev: NoteState, text: string): NoteState {
  return { text, source: text.trim() === '' ? 'manual' : prev.source }
}

/** 編集中の知識に関わる会話（新規作成中は、まだ結び付いていない会話）。 */
export function chatsFor(chats: KnowledgeChatSummary[], artifactId: string | null): KnowledgeChatSummary[] {
  return chats.filter((c) => c.artifact_id === artifactId)
}

/** 届いた出来事を、待っている間の表示に重ねる（思考の要約は足し、調べたことは並べる）。 */
export function withStreamEvent(s: ChatStreaming, event: ChatStreamEvent): ChatStreaming {
  if (event.type === 'delta') return { ...s, reply: s.reply + event.text }
  if (event.type === 'thought') return { ...s, thought: (s.thought ?? '') + event.text }
  if (event.type === 'tool') return { ...s, research: [...(s.research ?? []), event.text] }
  return s
}

/** SSE の 1 イベントを、ストリームの出来事にする。知らない種類・壊れた JSON は null（読み飛ばす）。 */
export function parseChatStreamEvent(msg: SseMessage): ChatStreamEvent | null {
  let data: unknown
  try {
    data = JSON.parse(msg.data)
  } catch {
    return null
  }
  if (!data || typeof data !== 'object') return null
  const d = data as Record<string, unknown>
  switch (msg.event) {
    case 'status':
      return typeof d.state === 'string' ? { type: 'status', state: d.state } : null
    case 'delta':
      return typeof d.text === 'string' ? { type: 'delta', text: d.text } : null
    case 'draft':
      return typeof d.field === 'string' && typeof d.text === 'string' ? { type: 'draft', field: d.field, text: d.text } : null
    case 'thought':
    case 'tool':
      return typeof d.text === 'string' ? { type: msg.event, text: d.text } : null
    case 'done':
      return { type: 'done', turn: data as ChatTurn }
    case 'error':
      return {
        type: 'error',
        code: typeof d.code === 'string' ? d.code : 'chat_failed',
        message: typeof d.message === 'string' ? d.message : 'AI の応答を得られませんでした。もう一度試してください',
        status: typeof d.status === 'number' ? d.status : 502,
      }
    default:
      return null
  }
}

/** 会話の API の失敗の文。サーバは固定の文だけを返すので、それを出す。 */
export function describeChatError(err: unknown): string {
  if (err instanceof StreamInterruptedError) return err.message
  if (err instanceof ApiError) {
    if (err.status === 404 && err.code === 'chat_not_found') return '会話が見つかりません（消された可能性があります）'
    if (err.code === 'message_too_long') return `発言が長すぎます。${MESSAGE_MAX_BYTES} バイト（日本語でおよそ 2,700 文字）以内にしてください`
    return err.message
  }
  return '通信に失敗しました。時間をおいて、もう一度試してください'
}
