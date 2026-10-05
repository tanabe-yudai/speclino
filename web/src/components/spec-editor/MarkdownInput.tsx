import { Label } from '@/components/ui/label'
import type { MarkdownInputProps } from '@/types/specEditor'

/** 本文の入力欄（等幅の textarea）。重いエディタは使わない。 */
export function MarkdownInput({ id, label, value, onChange, describedBy, invalid, rows = 28, textareaRef }: MarkdownInputProps) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <textarea
        id={id}
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        spellCheck={false}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        className="w-full resize-y rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-[13px] leading-5 outline-none [overflow-wrap:normal] whitespace-pre focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive dark:bg-input/30"
      />
    </div>
  )
}

