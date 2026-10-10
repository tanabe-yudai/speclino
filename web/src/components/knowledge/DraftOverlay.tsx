import { useEffect, useMemo, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { FOLLOW_SLACK_PX, draftDiff, nearBottom } from '@/services/chatDraft'
import type { DraftOverlayProps } from '@/types/chatDraft'

const OP_CLASS = { same: '', add: 'bg-ok-bg/60', del: 'bg-bad-bg/60' } as const
const OP_MARK = { same: ' ', add: '+', del: '-' } as const

/**
 * AI が書いている本文を、会話の返答の下に見せる（S76、S79 で編集欄の場所から移した）。送った時点の
 * 本文との行差分で、変わっている末尾を追う。編集欄の内容は変えない（確定したときに、親がまとめて反映する）。
 */
export function DraftOverlay({ draft, onStop }: DraftOverlayProps) {
  const diff = useMemo(() => draftDiff(draft.before, draft.text), [draft.before, draft.text])
  const box = useRef<HTMLDivElement>(null)
  const anchor = useRef<HTMLTableRowElement>(null)
  const anchorIndex = diff?.anchor ?? -1
  // 自分で動かした位置から離れたら（読み戻し）追わない。末尾の近くに戻れば、また追う
  const following = useRef(true)
  const lastSet = useRef(0)
  useEffect(() => {
    const el = box.current
    const row = anchor.current
    if (!el || !row || !following.current) return
    el.scrollTop = Math.max(0, row.offsetTop + row.offsetHeight - el.clientHeight)
    lastSet.current = el.scrollTop
  }, [anchorIndex, draft.text])
  const onScroll = () => {
    const el = box.current
    if (el) following.current = nearBottom(el) || Math.abs(el.scrollTop - lastSet.current) <= FOLLOW_SLACK_PX
  }

  return (
    <div className="mt-2 space-y-2 border-t pt-2" role="region" aria-label="AI が書いている本文">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold" role="status">
          AI が書いています…
        </span>
        <span className="text-xs text-ink-3">反映は書き終えてからです。編集欄はまだ変わっていません。</span>
        <Button type="button" size="sm" variant="secondary" className="ml-auto" onClick={onStop}>
          止める
        </Button>
      </div>
      <div ref={box} onScroll={onScroll} className="max-h-[min(20rem,40dvh)] overflow-auto rounded-md border bg-card" role="region" aria-label="書いている途中の差分" tabIndex={0}>
        {diff ? (
          <table className="w-full font-mono text-[12px] leading-5">
            <tbody>
              {diff.lines.map((l, i) => (
                <tr key={i} ref={i === diff.anchor ? anchor : undefined} className={OP_CLASS[l.op]}>
                  <td className={cn('w-4 pl-2 select-none', l.op === 'add' && 'text-ok', l.op === 'del' && 'text-bad')} aria-label={l.op === 'add' ? '追加' : l.op === 'del' ? '削除' : undefined}>
                    {OP_MARK[l.op]}
                  </td>
                  <td className="pr-2 break-all whitespace-pre-wrap">{l.text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <pre className="p-2 font-mono text-[12px] leading-5 whitespace-pre-wrap">{draft.text}</pre>
        )}
      </div>
    </div>
  )
}
