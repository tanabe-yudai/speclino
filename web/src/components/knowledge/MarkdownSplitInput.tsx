import { useState } from 'react'
import { MarkdownPreview } from '@/components/knowledge/MarkdownPreview'
import { MarkdownInput } from '@/components/spec-editor/MarkdownInput'
import { PaneViewSwitch } from '@/components/knowledge/PaneViewSwitch'
import { cn } from '@/lib/utils'
import { initialPaneView } from '@/services/paneView'
import type { PaneView } from '@/types/paneView'
import type { MarkdownSplitInputProps } from '@/types/specEditor'

/** 本文の入力欄とプレビュー。lg 以上は左右に並べ、それより狭い画面は 本文 / プレビュー の切り替え。 */
export function MarkdownSplitInput({ skillMd, ...input }: MarkdownSplitInputProps) {
  const [mode, setMode] = useState<PaneView>(() => initialPaneView(input.value))
  return (
    <div className="space-y-2">
      <PaneViewSwitch view={mode} onChange={setMode} />
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
