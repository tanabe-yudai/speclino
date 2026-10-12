import { useState } from 'react'
import { toast } from 'sonner'
import { retireArtifact } from '@/api/knowledge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { describeKnowledgeError, retireState } from '@/services/knowledge'
import type { RetirePanelProps } from '@/types/specEditor'

/**
 * 有効な改訂の取り下げ（bundle から外す）。改訂は履歴に残り、新しい改訂を承認すれば戻せる。
 * ボタンは owner / approver にだけ出し、押すと確認のダイアログで影響を伝える。
 */
export function RetirePanel({ slug, active, dirty, onRetired, onConflict }: RetirePanelProps) {
  const workspace = useCurrentWorkspace()
  const [confirming, setConfirming] = useState(false)
  const [retiring, setRetiring] = useState(false)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const state = retireState(workspace?.role ?? null, active, dirty)

  if (!active || !state.visible) return null

  function retire() {
    if (!active) return
    setConfirming(false)
    setRetiring(true)
    setError(null)
    retireArtifact(slug, active.artifact_id, active.revision, active.content_hash, reason)
      .then((result) => {
        toast.success(`改訂 ${result.revision.revision} を取り下げました。以後の配信と検査には使われません`)
        onRetired(result.bundle.generation)
      })
      .catch((err: unknown) => {
        const described = describeKnowledgeError(err)
        if (described.kind === 'conflict') onConflict()
        else setError(described.message)
      })
      .finally(() => setRetiring(false))
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button type="button" variant="outline" size="sm" onClick={() => setConfirming(true)} disabled={!state.enabled || retiring} aria-describedby={state.reason ? 'retire-reason' : undefined}>
        {retiring ? '取り下げています…' : '取り下げる'}
      </Button>
      {state.reason && (
        <span id="retire-reason" className="text-sm text-ink-2">
          {state.reason}
        </span>
      )}
      {error && (
        <p role="alert" className="w-full text-sm text-bad">
          {error}
        </p>
      )}
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {active.name} の改訂 {active.revision} を取り下げますか？
            </DialogTitle>
            <DialogDescription>
              取り下げると、以後の配信（sync・MCP）と検査に使われなくなり、次の取り込みで各リポジトリのファイルも消えます。改訂は履歴に残り、保存して承認すれば戻せます。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="retire-reason-input">理由（任意。監査に残ります）</Label>
            <Textarea id="retire-reason-input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} rows={3} />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="secondary">やめる</Button>
            </DialogClose>
            <Button variant="destructive" onClick={retire}>
              取り下げる
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
