import { MarkdownSplitInput } from '@/components/knowledge/MarkdownSplitInput'
import type { InstructionsContent } from '@/types/knowledge'
import type { ContentEditorProps } from '@/types/specEditor'

/** 指示書（AGENTS.md / CLAUDE.md の SpecLino の管理区画に書き出す本文）。書き出し先は取り込むエージェントが選ぶ。 */
export function InstructionsEditorPane({ content, onChange }: ContentEditorProps<InstructionsContent>) {
  return (
    <div className="space-y-4">
      <MarkdownSplitInput id="instructions-body" label="本文" value={content.body} onChange={(body) => onChange({ ...content, body })} rows={24} />
    </div>
  )
}
