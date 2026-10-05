import { MarkdownInput } from '@/components/spec-editor/MarkdownInput'
import { Label } from '@/components/ui/label'
import { TARGET_LABELS } from '@/services/knowledge'
import type { InstructionsContent, InstructionsTarget } from '@/types/knowledge'
import type { ContentEditorProps } from '@/types/specEditor'

/** 指示書（AGENTS.md / CLAUDE.md の SpecLino の管理区画に書き出す本文）。 */
export function InstructionsEditorPane({ content, onChange }: ContentEditorProps<InstructionsContent>) {
  return (
    <div className="space-y-4">
      <div className="grid max-w-xs gap-1.5">
        <Label htmlFor="instructions-target">書き出し先</Label>
        <select
          id="instructions-target"
          className="h-9 rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30"
          value={content.target}
          onChange={(e) => onChange({ ...content, target: e.target.value as InstructionsTarget })}
        >
          {(Object.keys(TARGET_LABELS) as InstructionsTarget[]).map((t) => (
            <option key={t} value={t}>
              {TARGET_LABELS[t]}
            </option>
          ))}
        </select>
      </div>
      <MarkdownInput id="instructions-body" label="本文" value={content.body} onChange={(body) => onChange({ ...content, body })} rows={24} />
    </div>
  )
}
