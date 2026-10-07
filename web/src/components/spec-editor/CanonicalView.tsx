import type { CanonicalViewProps } from '@/types/specEditor'

/** IR から作った正規形（S63 の render）。承認者は原文とこれを並べて意味を確かめる（設計 4.3）。 */
export function CanonicalView({ canonical, pending, hasErrors }: CanonicalViewProps) {
  if (canonical === null) {
    return (
      <p className="text-sm text-ink-3">
        {pending ? '変換しています…' : hasErrors ? '診断にエラーがあるため、整形できません。' : 'まだ変換していません。'}
      </p>
    )
  }
  return (
    <pre
      className="max-h-[36rem] overflow-auto rounded-md bg-muted p-3 font-mono text-[12px] leading-5 [overflow-wrap:normal] whitespace-pre"
      aria-label="整形した本文"
      tabIndex={0}
    >
      {canonical}
    </pre>
  )
}
