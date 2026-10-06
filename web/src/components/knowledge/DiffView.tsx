import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { diffLines } from '@/services/textDiff'
import type { DiffViewProps } from '@/types/specEditor'

const OP_CLASS = { same: '', add: 'bg-ok-bg/60', del: 'bg-bad-bg/60' } as const
const OP_MARK = { same: ' ', add: '+', del: '-' } as const

/** 行単位の差分。大きすぎるときは差分を出さない（間違った差分を見せない）。 */
export function DiffView({ before, after, label }: DiffViewProps) {
  const lines = useMemo(() => diffLines(before, after), [before, after])
  if (lines === null) return <p className="text-sm text-unk">大きすぎて差分を出せません。両方の本文を開いて確かめてください。</p>
  const changed = lines.filter((l) => l.op !== 'same').length
  if (changed === 0) return <p className="text-sm text-ink-3">違いはありません。</p>
  return (
    <div className="max-h-[40rem] overflow-auto rounded-md border" tabIndex={0} role="region" aria-label={label}>
      <table className="w-full font-mono text-[12px] leading-5">
        <tbody>
          {lines.map((l, i) => (
            <tr key={i} className={OP_CLASS[l.op]}>
              <td className="w-10 pr-2 text-right text-ink-3 select-none">{l.oldNo ?? ''}</td>
              <td className="w-10 pr-2 text-right text-ink-3 select-none">{l.newNo ?? ''}</td>
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
