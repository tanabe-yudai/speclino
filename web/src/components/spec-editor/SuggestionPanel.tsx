import { useMemo } from 'react'
import { ToneBadge } from '@/components/app/ToneBadge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { excerpt } from '@/services/suggestions'
import { diffLines } from '@/services/textDiff'
import type { PendingSuggestion, SuggestionPanelProps } from '@/types/suggestion'

const OP_CLASS = { same: '', add: 'bg-ok-bg/60', del: 'bg-bad-bg/60' } as const
const OP_MARK = { same: ' ', add: '+', del: '-' } as const

function ExcerptDiff({ text, suggestion }: { text: string; suggestion: PendingSuggestion }) {
  const ex = useMemo(() => excerpt(text, suggestion), [text, suggestion])
  const lines = useMemo(() => diffLines(ex.before, ex.after), [ex])
  if (lines === null) return <p className="text-sm text-unk">大きすぎて差分を出せません。</p>
  const offset = ex.firstLine - 1
  return (
    <div className="max-h-72 overflow-auto rounded-md border" tabIndex={0} role="region" aria-label={`提案の差分（${ex.firstLine} 行目から）`}>
      <table className="w-full font-mono text-[12px] leading-5">
        <tbody>
          {lines.map((l, i) => (
            <tr key={i} className={OP_CLASS[l.op]}>
              <td className="w-10 pr-2 text-right text-ink-3 select-none">{l.oldNo === null ? '' : l.oldNo + offset}</td>
              <td className={cn('w-4 select-none', l.op === 'add' && 'text-ok', l.op === 'del' && 'text-bad')} aria-label={l.op === 'add' ? '追加' : l.op === 'del' ? '削除' : undefined}>
                {OP_MARK[l.op]}
              </td>
              <td className="pr-2 break-all whitespace-pre-wrap">{l.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * AI の書き換え提案（S67、設計 D5）。提案は本文の置換の案だけで、IR・判定・承認には関わらない。
 * 表示だけを受け持ち、状態は `useSuggestions`（エディタの側）が持つ（タブを切り替えても失わない）。
 * 「適用」は編集欄の本文だけを変える（保存と承認は人が行う）。適用すると重ならない提案は位置をずらして残し、
 * 重なる提案は外す。提案を受け取った後に本文を手で変えたら、残りは古いものとして適用できなくする。
 */
export function SuggestionPanel({ state, stale, blockedReason, onRequest, onApply, onReject }: SuggestionPanelProps) {
  const ready = state.kind === 'ready' ? state : null
  const loading = state.kind === 'loading'
  return (
    <section className="space-y-3" aria-labelledby="suggestion-heading">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id="suggestion-heading" className="text-sm font-semibold">
          AI の書き換え提案
        </h3>
        <Button type="button" size="sm" variant="outline" onClick={onRequest} disabled={loading || blockedReason !== null} aria-describedby="suggestion-help">
          {loading ? '提案を求めています…' : 'AI に提案を求める'}
        </Button>
      </div>
      <p id="suggestion-help" className="text-xs text-ink-3">
        {blockedReason ??
          'AI は本文の書き換えを提案するだけです。機械可読な形（IR）は SpecLino の parser が本文から作ります。適用しても編集欄が変わるだけで、保存と承認はあなたが行います。'}
      </p>
      <div aria-live="polite">
        {state.kind === 'error' && (
          <p role="alert" className="text-sm text-bad">
            {state.message}
          </p>
        )}
        {ready && (
          <p className="text-xs text-ink-3">
            提案を求めたときのエラー {ready.errorsBefore} 件 · 残りの提案 {ready.items.length} 件
            {ready.dropped > 0 && ` · 適用した提案と重なって外した提案 ${ready.dropped} 件`}
            。各提案のエラー数は、求めたときの本文にその提案だけを当てて parser で検証し直した値です。
          </p>
        )}
        {stale && ready && ready.items.length > 0 && (
          <p role="status" className="mt-1 text-sm text-unk">
            提案を受け取った後に本文が変わったため、残りの提案は適用できません。もう一度提案を求めてください。
          </p>
        )}
      </div>
      {ready && ready.items.length === 0 && <p className="text-sm text-ink-3">表示する提案はありません。</p>}
      {ready && ready.items.length > 0 && (
        <ol className="space-y-4">
          {ready.items.map((s, i) => (
            <li key={s.id} className="space-y-2 rounded-md border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">提案 {i + 1}</span>
                <ToneBadge tone={s.after.compiles ? 'ok' : 'unk'}>
                  エラー {s.after.errors_before} → {s.after.errors_after} 件
                </ToneBadge>
              </div>
              <p className="text-sm break-words whitespace-pre-wrap">{s.reason}</p>
              <ExcerptDiff text={ready.base} suggestion={s} />
              <div className="flex gap-2">
                <Button type="button" size="sm" onClick={() => onApply(s)} disabled={stale}>
                  適用
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => onReject(s)}>
                  却下
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
