import { useState } from 'react'
import { MarkdownPreview } from '@/components/knowledge/MarkdownPreview'
import { MarkdownInput } from '@/components/spec-editor/MarkdownInput'
import { cn } from '@/lib/utils'
import type { MarkdownSplitInputProps } from '@/types/specEditor'

type Mode = 'edit' | 'preview'
const MODES: { value: Mode; label: string }[] = [
  { value: 'edit', label: '編集' },
  { value: 'preview', label: 'プレビュー' },
]

/** 本文の入力欄とプレビュー。lg 以上は左右に並べ、それより狭い画面は 編集 / プレビュー の切り替え。 */
export function MarkdownSplitInput({ skillMd, ...input }: MarkdownSplitInputProps) {
  const [mode, setMode] = useState<Mode>('edit')
  return (
    <div className="space-y-2">
      <div role="group" aria-label="表示の切り替え" className="inline-flex rounded-lg border p-0.5 lg:hidden">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            aria-pressed={mode === m.value}
            onClick={() => setMode(m.value)}
            className={cn('rounded-md px-3 py-1 text-sm', mode === m.value ? 'bg-muted font-medium' : 'text-ink-3')}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className={cn('min-w-0', mode !== 'edit' && 'hidden lg:block')}>
          <MarkdownInput {...input} />
        </div>
        <div className={cn('grid min-w-0 grid-rows-[auto_1fr] gap-1.5', mode !== 'preview' && 'hidden lg:grid')}>
          <span className="hidden text-sm leading-none font-medium select-none lg:inline">プレビュー</span>
          <div className="relative min-h-64 rounded-lg border">
            <div className="overflow-auto p-3 lg:absolute lg:inset-0" role="region" aria-label="プレビュー">
              <MarkdownPreview markdown={input.value} skillMd={skillMd} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
