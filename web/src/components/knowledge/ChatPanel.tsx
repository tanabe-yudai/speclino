import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { DiffView } from '@/components/knowledge/DiffView'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/services/format'
import { CHAT_NOTICE, MESSAGE_MAX_BYTES, errorCount, messageBytesLeft, textHash } from '@/services/knowledgeChat'
import type { ChatMessage, ChatPanelProps, ChatProposal, RawProposal } from '@/types/knowledgeChat'

/** 今の編集欄の本文のハッシュ（提案の土台と比べて、古い提案を反映させない）。計算中は null。 */
function useTextHash(text: string): string | null {
  const [state, setState] = useState<{ text: string; hash: string } | null>(null)
  useEffect(() => {
    let alive = true
    textHash(text).then((hash) => alive && setState({ text, hash }))
    return () => {
      alive = false
    }
  }, [text])
  return state && state.text === text ? state.hash : null
}

function ProposalView({
  proposal,
  currentHash,
  beforeText,
  afterText,
  onApply,
}: {
  proposal: ChatProposal
  currentHash: string | null
  beforeText: (raw: RawProposal) => string
  afterText: (raw: RawProposal) => string
  onApply: (raw: RawProposal) => void
}) {
  const [showDiff, setShowDiff] = useState(true)
  const errors = errorCount(proposal)
  const stale = currentHash !== null && currentHash !== proposal.base_hash
  return (
    <div className="mt-2 space-y-2 rounded-md border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold">下書きの提案</span>
        {errors > 0 ? <ToneBadge tone="bad">診断のエラー {errors} 件</ToneBadge> : <ToneBadge tone="ok">診断のエラーなし</ToneBadge>}
        {stale && <ToneBadge tone="unk">編集欄が変わったため古い提案</ToneBadge>}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="secondary" onClick={() => setShowDiff(!showDiff)} aria-expanded={showDiff}>
            {showDiff ? '差分を閉じる' : '差分を見る'}
          </Button>
          <Button type="button" size="sm" onClick={() => onApply(proposal.raw_proposal)} disabled={currentHash === null || stale}>
            エディタに反映
          </Button>
        </div>
      </div>
      <p className="text-xs text-ink-3">
        {stale
          ? 'この提案の後に編集欄が変わりました。今の本文で、もう一度頼んでください。'
          : '反映しても編集欄が変わるだけです。保存と承認はあなたが行います。'}
      </p>
      {proposal.diagnostics.length > 0 && (
        <ul className="space-y-0.5 text-xs" aria-label="提案の診断">
          {proposal.diagnostics.map((d, i) => (
            <li key={i} className={cn(d.severity === 'error' ? 'text-bad' : 'text-ink-2')}>
              <span className="font-mono">{d.code}</span> {d.message}
            </li>
          ))}
        </ul>
      )}
      {showDiff && <DiffView label="提案の差分" before={beforeText(proposal.raw_proposal)} after={afterText(proposal.raw_proposal)} />}
    </div>
  )
}

function Message({ m, children }: { m: ChatMessage; children?: ReactNode }) {
  const mine = m.role === 'user'
  return (
    <li className={cn('rounded-md px-3 py-2 text-sm', mine ? 'ml-6 bg-muted' : 'mr-6 border bg-background')}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-ink-3">
        <span className="font-semibold">{mine ? 'あなた' : 'AI'}</span>
        <span>{formatDateTime(m.created_at)}</span>
        {m.status === 'pending' && <ToneBadge tone="rev">返答を待っています</ToneBadge>}
        {m.status === 'failed' && <ToneBadge tone="bad">返答を得られませんでした</ToneBadge>}
      </div>
      <p className="break-words whitespace-pre-wrap">{m.text}</p>
      {children}
    </li>
  )
}

/**
 * AI と知識を作るパネル（S73、設計 5 章）。会話は本人だけのもの。AI の返答は説明と下書きの提案で、
 * 「エディタに反映」は編集欄だけを変える（保存・承認は人）。提案は送ったときの編集欄の本文に
 * 結び付き、本文が変わった後の古い提案は反映できない。
 */
export function ChatPanel({ chat, editorText, beforeText, afterText, onApply }: ChatPanelProps) {
  const [text, setText] = useState('')
  const currentHash = useTextHash(editorText)
  const current = chat.current
  const left = messageBytesLeft(text.trim())

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const body = text.trim()
    if (!body || chat.sending || messageBytesLeft(body) < 0) return
    // 失敗したら本文を残す（同じ本文の再送は同じ冪等キーで送る）
    chat.send(body, editorText).then((ok) => ok && setText(''))
  }

  return (
    <Panel className="space-y-4" aria-labelledby="chat-heading">
      <div className="space-y-1">
        <PanelTitle id="chat-heading">AI と作る</PanelTitle>
        <p className="text-xs text-ink-2">{CHAT_NOTICE}</p>
      </div>

      <div className="grid gap-4 md:grid-cols-[14rem_minmax(0,1fr)]">
        <nav aria-label="自分の会話" className="min-w-0 space-y-2">
          <Button type="button" size="sm" variant="secondary" className="w-full" onClick={chat.startNew} disabled={chat.sending}>
            新しい会話
          </Button>
          {chat.listError && <p className="text-xs text-bad">{chat.listError}</p>}
          {chat.chats.length === 0 ? (
            <p className="text-xs text-ink-3">まだ会話はありません。</p>
          ) : (
            <ul className="space-y-1">
              {chat.chats.map((c) => (
                <li key={c.id} className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => chat.select(c.id)}
                    aria-pressed={current?.id === c.id}
                    className={cn('min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted', current?.id === c.id && 'bg-muted font-semibold')}
                  >
                    {c.title || '（題なし）'} <span className="text-ink-3">· {c.message_count} 件</span>
                  </button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`会話「${c.title || '題なし'}」を削除`}
                    onClick={() => {
                      if (window.confirm('この会話を削除しますか？元に戻せません。')) void chat.remove(c.id)
                    }}
                  >
                    削除
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </nav>

        <div className="min-w-0 space-y-3">
          {current && current.messages.length > 0 ? (
            <ol className="max-h-[36rem] space-y-2 overflow-auto" aria-label="会話" aria-live="polite">
              {current.messages.map((m) => (
                <Message key={m.id} m={m}>
                  {m.proposal && <ProposalView proposal={m.proposal} currentHash={currentHash} beforeText={beforeText} afterText={afterText} onApply={onApply} />}
                </Message>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-ink-3">作りたいものを書いて送ってください。例: 「社内の CLI ツール foo の使い方をスキルにしたい」</p>
          )}
          <form className="space-y-2" onSubmit={submit}>
            <Label htmlFor="chat-input" className="sr-only">
              AI への発言
            </Label>
            <Textarea
              id="chat-input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              aria-describedby="chat-input-left"
              aria-invalid={left < 0 || undefined}
              placeholder="AI への発言（今の編集欄の本文も一緒に送ります）"
              disabled={chat.sending}
            />
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" size="sm" disabled={chat.sending || !text.trim() || left < 0}>
                {chat.sending ? '返答を待っています…' : '送る'}
              </Button>
              <span id="chat-input-left" className={cn('text-xs', left < 0 ? 'text-bad' : 'text-ink-3')}>
                {left < 0 ? `${-left} バイト超えています（${MESSAGE_MAX_BYTES} バイトまで）` : `残り ${left} バイト`}
              </span>
              {chat.error && (
                <p role="alert" className="text-sm text-bad">
                  {chat.error}
                </p>
              )}
            </div>
          </form>
        </div>
      </div>
    </Panel>
  )
}
