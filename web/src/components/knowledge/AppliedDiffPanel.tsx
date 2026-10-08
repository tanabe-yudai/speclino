import { Panel } from '@/components/app/Panel'
import { DiffView } from '@/components/knowledge/DiffView'
import { Button } from '@/components/ui/button'
import type { AppliedDiffPanelProps } from '@/types/chatDraft'

/** AI が書いた本文を編集欄に反映した直後の差分（反映前と後）。元に戻す / 閉じる。 */
export function AppliedDiffPanel({ applied, undoable, onUndo, onClose }: AppliedDiffPanelProps) {
  return (
    <Panel className="space-y-2" aria-label="AI が反映した差分">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold">AI が本文を書き換えました</span>
        <span className="text-xs text-ink-3">まだ保存していません。</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="secondary" onClick={onUndo} disabled={!undoable}>
            元に戻す
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            閉じる
          </Button>
        </div>
      </div>
      {!undoable && <p className="text-xs text-ink-3">反映後に編集したため戻せません</p>}
      <DiffView label="反映した差分" before={applied.before} after={applied.after} />
    </Panel>
  )
}
