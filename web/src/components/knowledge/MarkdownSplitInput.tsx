import { useState } from 'react'
import { MarkdownPreview } from '@/components/knowledge/MarkdownPreview'
import { MarkdownInput } from '@/components/spec-editor/MarkdownInput'
import { PaneViewSwitch } from '@/components/knowledge/PaneViewSwitch'
import { cn } from '@/lib/utils'
import { initialPaneView } from '@/services/paneView'
import type { PaneView } from '@/types/paneView'
import type { MarkdownSplitInputProps } from '@/types/specEditor'

/** 本文の入力欄とプレビュー。画面の幅によらず 本文 / プレビュー を切り替える。 */
export function MarkdownSplitInput({ skillMd, ...input }: MarkdownSplitInputProps) {
  const [mode, setMode] = useState<PaneView>(() => initialPaneView(input.value))
  return (
    <div className="space-y-2">
      <PaneViewSwitch view={mode} onChange={setMode} />
      <div>
        <div className={cn('min-w-0', mode !== 'edit' && 'hidden')}>
          <MarkdownInput {...input} />
        </div>
        <div className={cn('min-w-0', mode !== 'preview' && 'hidden')}>
          <div className="min-h-64 rounded-lg border">
            <div className="overflow-auto p-3" role="region" aria-label="プレビュー">
              <MarkdownPreview markdown={input.value} skillMd={skillMd} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
