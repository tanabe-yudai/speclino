import { Loader2 } from 'lucide-react'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { DiffView } from '@/components/knowledge/DiffView'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useFollowBottom } from '@/hooks/useFollowBottom'
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion'
import { cn } from '@/lib/utils'
import { skipMessage } from '@/services/chatDraft'
import { formatDateTime } from '@/services/format'
import { CHAT_QUESTION_LABELS, CHAT_SOURCE_LABELS } from '@/services/labels'
import { CHAT_NOTICE, MESSAGE_MAX_BYTES, errorCount, messageBytesLeft, pickableReplyId, textHash } from '@/services/knowledgeChat'
import type { ChatDraft, ProposalOutcome } from '@/types/chatDraft'
import type { ChatMessage, ChatPanelProps, ChatProposal, ChatQuestion, ChatSource, ChatStreaming, RawProposal } from '@/types/knowledgeChat'

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

/**
 * 知識の編集画面での提案の 1 行。本文は編集欄に流し込み済みか、理由つきで未反映。
 * 再読込の後は扱いが分からないので、差分と「本文に反映」だけを出す。
 */
function ProposalLine({
  proposal,
  outcome,
  currentHash,
  beforeText,
  afterText,
  onApply,
}: {
  proposal: ChatProposal
  outcome: ProposalOutcome | undefined
  currentHash: string | null
  beforeText: (raw: RawProposal) => string
  afterText: (raw: RawProposal) => string
  onApply: (raw: RawProposal) => void
}) {
  const [showDiff, setShowDiff] = useState(false)
  const stale = currentHash !== null && currentHash !== proposal.base_hash
  const applied = outcome?.status === 'applied'
  const summary = applied ? '本文に反映しました' : outcome ? `反映していません（${skipMessage(outcome.reason, errorCount(proposal))}）` : '下書きの提案があります'
  return (
    <div className="mt-2 space-y-2 border-t pt-2" data-testid="proposal-line">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={cn('font-semibold', outcome?.status === 'skipped' && 'text-unk')}>{summary}</span>
        <Button type="button" size="sm" variant="ghost" onClick={() => setShowDiff(!showDiff)} aria-expanded={showDiff}>
          {showDiff ? '差分を閉じる' : '差分を見る'}
        </Button>
        {/* 反映した直後は本文が提案の土台と違うのが当然なので、再反映の導線も「古い」の注記も出さない */}
        {!(applied && stale) && (
          <Button type="button" size="sm" variant="secondary" onClick={() => onApply(proposal.raw_proposal)} disabled={currentHash === null || stale}>
            {applied ? 'もう一度反映' : '本文に反映'}
          </Button>
        )}
        {stale && !applied && <span className="text-ink-3">編集欄が変わったため古い提案です</span>}
      </div>
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

/** 返答の出典（AI が実際に読んだもの・検索の出典。サーバが組み立てる）。 */
function Sources({ sources }: { sources: ChatSource[] }) {
  return (
    <div className="mt-2 border-t pt-2 text-xs">
      <div className="mb-1 font-semibold text-ink-2">出典</div>
      <ul className="space-y-0.5" aria-label="出典">
        {sources.map((s) => (
          <li key={s.ref} className="flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 text-ink-3">{CHAT_SOURCE_LABELS[s.kind]}</span>
            {s.url ? (
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="min-w-0 truncate underline">
                {s.title}
              </a>
            ) : (
              <span className="min-w-0 truncate">{s.title}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Google 検索の候補の表示（検索を使った返答に出す決まり）。中身は Google の HTML なので、
 * スクリプトを動かさない sandbox の iframe に入れ、リンクだけ新しいタブで開けるようにする。
 */
function SearchSuggestions({ items }: { items: string[] }) {
  return (
    <div className="mt-2 space-y-1">
      {items.map((html, i) => (
        <iframe
          key={i}
          title="Google 検索の候補"
          sandbox="allow-popups allow-popups-to-escape-sandbox"
          srcDoc={`<base target="_blank">${html}`}
          className="h-16 w-full border-0"
        />
      ))}
    </div>
  )
}

/**
 * AI からの質問と選択肢。最新の返答だけ選択肢を押せ、押すとその文をそのまま発言として送る
 * （自由入力でも答えられる）。スマホで押しやすいよう、選択肢は高さ 44px 以上で折り返す。
 */
function Questions({ questions, onPick }: { questions: ChatQuestion[]; onPick?: (option: string) => void }) {
  return (
    <div className="mt-2 space-y-2 border-t pt-2" data-testid="chat-questions">
      <div className="text-xs font-semibold text-ink-2">{CHAT_QUESTION_LABELS.heading}</div>
      <ol className="space-y-2">
        {questions.map((q, i) => (
          <li key={i} className="space-y-1.5">
            <p className="break-words">{q.question}</p>
            {onPick && q.options.length > 0 && (
              <div className="flex flex-col gap-1.5 sm:flex-row sm:flex-wrap" role="group" aria-label={q.question}>
                {q.options.map((o) => (
                  <Button key={o} type="button" variant="outline" className="h-auto min-h-11 justify-start px-3 py-2 text-left whitespace-normal sm:min-h-9" onClick={() => onPick(o)}>
                    {o}
                  </Button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ol>
      {onPick && <p className="text-xs text-ink-3">{CHAT_QUESTION_LABELS.hint}</p>}
    </div>
  )
}

function Message({ m, children, onPick }: { m: ChatMessage; children?: ReactNode; onPick?: (option: string) => void }) {
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
      {m.questions && m.questions.length > 0 && <Questions questions={m.questions} onPick={onPick} />}
      {m.sources && m.sources.length > 0 && <Sources sources={m.sources} />}
      {m.search_suggestions && m.search_suggestions.length > 0 && <SearchSuggestions items={m.search_suggestions} />}
    </li>
  )
}

/** 返答を待っている印。動きを減らす設定のときは動かさない。 */
function ThinkingDots() {
  const reduced = usePrefersReducedMotion()
  return (
    <span className="inline-flex items-center gap-1 text-ink-3" role="status">
      考えています
      <span aria-hidden="true" className="inline-flex">
        {[0, 1, 2].map((i) => (
          <span key={i} className={reduced ? undefined : 'animate-pulse'} style={reduced ? undefined : { animationDelay: `${i * 200}ms` }}>
            ・
          </span>
        ))}
      </span>
    </span>
  )
}

/** 本文を書いている間の状態表示。差分は出さず、書き終えてから反映する（編集欄はまだ変わらない）。 */
function WritingStatus({ onStop }: { onStop: () => void }) {
  const reduced = usePrefersReducedMotion()
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 border-t pt-2 text-sm text-ink-2" role="status">
      <Loader2 aria-hidden="true" className={cn('size-4', !reduced && 'animate-spin')} />
      <span>本文を編集中…</span>
      <Button type="button" size="sm" variant="secondary" className="ml-auto" onClick={onStop}>
        止める
      </Button>
    </div>
  )
}

/** AI の思考の要約（折りたたみ。表示だけで、会話には残らない）。 */
function ThoughtSummary({ text }: { text: string }) {
  return (
    <details className="mb-2 text-xs text-ink-2">
      <summary className="cursor-pointer select-none">考えていること</summary>
      <p className="mt-1 max-h-40 overflow-auto break-words whitespace-pre-wrap">{text}</p>
    </details>
  )
}

/** 調べたこと（古い順）。本文を書き始めるまでは、最後の行が今調べていること。 */
function ResearchStatus({ items, active }: { items: string[]; active: boolean }) {
  const reduced = usePrefersReducedMotion()
  return (
    <ul className="mt-2 space-y-0.5 border-t pt-2 text-xs text-ink-2" aria-label="調べたこと" role="status">
      {items.map((t, i) => {
        const now = active && i === items.length - 1
        return (
          <li key={i} className="flex items-center gap-2">
            {now ? <Loader2 aria-hidden="true" className={cn('size-3', !reduced && 'animate-spin')} /> : <span aria-hidden="true">・</span>}
            <span className={cn('break-words', now && 'text-foreground')}>{t}</span>
          </li>
        )
      })}
    </ul>
  )
}

/** 送った発言と、届いている途中の返答（返答が届くまでは「考えています」）と、本文を書いている間の状態。 */
function StreamingMessages({ streaming, draft, onStop }: { streaming: ChatStreaming; draft: ChatDraft | null; onStop: () => void }) {
  return (
    <>
      <li className="ml-6 rounded-md bg-muted px-3 py-2 text-sm" aria-live="off">
        <div className="mb-1 text-xs font-semibold text-ink-3">あなた</div>
        <p className="break-words whitespace-pre-wrap">{streaming.userText}</p>
      </li>
      <li className="mr-6 rounded-md border bg-background px-3 py-2 text-sm" aria-live="off" aria-busy="true">
        <div className="mb-1 text-xs font-semibold text-ink-3">AI</div>
        {streaming.thought && <ThoughtSummary text={streaming.thought} />}
        {streaming.reply ? <p className="break-words whitespace-pre-wrap">{streaming.reply}</p> : <ThinkingDots />}
        {streaming.research && streaming.research.length > 0 && <ResearchStatus items={streaming.research} active={!streaming.reply && !draft} />}
        {draft && <WritingStatus onStop={onStop} />}
      </li>
    </>
  )
}

/**
 * AI と知識を作るパネル（S73）。会話は本人だけのもの。AI の返答は説明と下書きの提案で、
 * 「エディタに反映」は編集欄だけを変える（保存・承認は人）。提案は送ったときの編集欄の本文に
 * 結び付き、本文が変わった後の古い提案は反映できない。
 */
export function ChatPanel({ chat, editorText, beforeText, afterText, onApply, inline, bodyText, outcomes, onReply }: ChatPanelProps) {
  const [text, setText] = useState('')
  const currentHash = useTextHash(editorText)
  const current = chat.current
  const left = messageBytesLeft(text.trim())
  // 新しい発言・返答の伸び・書いている本文の伸びで、一覧の末尾を追う（読み戻しているときは追わない）
  const { ref: listRef, onScroll: onListScroll, follow: followList } = useFollowBottom<HTMLOListElement>(
    `${current?.messages.length ?? 0}:${chat.streaming?.userText ?? ''}:${chat.streaming?.reply.length ?? -1}:${chat.draft?.text.length ?? -1}`,
    current?.id ?? null,
  )

  function send(body: string, onDone: (ok: boolean) => void) {
    if (!body || chat.sending || messageBytesLeft(body) < 0) return
    followList()
    chat.send(body, editorText, { bodyText, onTurn: (turn) => onReply?.(turn, editorText) }).then(onDone)
  }

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    // 失敗したら本文を残す（同じ本文の再送は同じ冪等キーで送る）
    send(text.trim(), (ok) => ok && setText(''))
  }

  // 選択肢はそのまま送る。失敗したら入力欄に入れて、送り直せるようにする
  const pick = (option: string) => send(option, (ok) => !ok && setText(option))
  const messages = current?.messages ?? []
  const pickable = pickableReplyId(messages, chat.sending)

  return (
    // 返答や差分が伸びても、ブラウザがページのスクロールを合わせて動かさない（S79）
    <Panel className="space-y-4 [overflow-anchor:none]" aria-labelledby="chat-heading">
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
          {(current && current.messages.length > 0) || chat.streaming ? (
            <ol ref={listRef} onScroll={onListScroll} className="max-h-[min(36rem,60dvh)] space-y-2 overflow-auto" aria-label="会話" aria-live="polite">
              {messages.map((m) => (
                <Message key={m.id} m={m} onPick={m.id === pickable ? pick : undefined}>
                  {m.proposal &&
                    (inline ? (
                      <ProposalLine proposal={m.proposal} outcome={outcomes?.[m.id]} currentHash={currentHash} beforeText={beforeText} afterText={afterText} onApply={onApply} />
                    ) : (
                      <ProposalView proposal={m.proposal} currentHash={currentHash} beforeText={beforeText} afterText={afterText} onApply={onApply} />
                    ))}
                </Message>
              ))}
              {chat.streaming && <StreamingMessages streaming={chat.streaming} draft={chat.draft} onStop={chat.stop} />}
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
              // 送信中も disabled にしない（スマホでキーボードが閉じて画面が動く）。書き換えだけ止める
              readOnly={chat.sending}
            />
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" size="sm" disabled={chat.sending || !text.trim() || left < 0}>
                {chat.sending ? '返答を待っています…' : '送る'}
              </Button>
              {chat.sending && (
                <Button type="button" size="sm" variant="secondary" onClick={chat.stop}>
                  止める
                </Button>
              )}
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
