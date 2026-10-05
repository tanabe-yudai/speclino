import { useState } from 'react'
import { toast } from 'sonner'
import { approveArtifact } from '@/api/knowledge'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useMe } from '@/hooks/useMe'
import { approvalState, describeKnowledgeError } from '@/services/knowledge'
import type { ApprovalPanelProps } from '@/types/specEditor'

/**
 * 保存済みの下書きの承認（設計 3.2）。承認者が見た番号と content_hash をサーバが照合する。
 * ボタンは owner / approver にだけ出し、作者本人なら自己承認の印が残ることを先に伝える。
 */
export function ApprovalPanel({ slug, draft, dirty, previewErrors, previewPending, previewFailed, onApproved, onConflict }: ApprovalPanelProps) {
  const { me } = useMe()
  const workspace = useCurrentWorkspace()
  const [confirming, setConfirming] = useState(false)
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const state = approvalState({ role: workspace?.role ?? null, uid: me?.uid ?? null, draft, dirty, previewErrors, previewPending, previewFailed })

  if (!draft) return null

  function approve() {
    if (!draft) return
    setConfirming(false)
    setApproving(true)
    setError(null)
    approveArtifact(slug, draft.artifact_id, draft.revision, draft.content_hash)
      .then((result) => {
        const self = result.revision.self_approved ? '（自己承認）' : ''
        toast.success(`改訂 ${result.revision.revision} を承認しました${self}。bundle の世代 ${result.bundle.generation}`)
        onApproved(result.bundle.generation)
      })
      .catch((err: unknown) => {
        const described = describeKnowledgeError(err)
        if (described.kind === 'conflict') onConflict()
        else setError(described.message)
      })
      .finally(() => setApproving(false))
  }

  return (
    <Panel className="space-y-3 border-rev/40 bg-rev-bg/40">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <PanelTitle>承認</PanelTitle>
        <ToneBadge tone="rev">下書き 改訂 {draft.revision}</ToneBadge>
        <span className="text-xs text-ink-3">
          作成 <span className="font-mono break-all">{draft.author}</span>
        </span>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-ink-3">承認する改訂</dt>
        <dd className="font-mono">{draft.revision}</dd>
        <dt className="text-ink-3">content_hash</dt>
        <dd className="font-mono break-all">{draft.content_hash}</dd>
      </dl>
      {!state.visible && <p className="text-sm text-ink-3">承認できるのは owner / approver です。</p>}
      {state.visible && (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => setConfirming(true)} disabled={!state.enabled || approving} aria-describedby={state.reason ? 'approve-reason' : undefined}>
            {approving ? '承認しています…' : '承認して有効にする'}
          </Button>
          {state.reason && (
            <span id="approve-reason" className="text-sm text-ink-2">
              {state.reason}
            </span>
          )}
        </div>
      )}
      {state.visible && state.selfApproval && (
        <p className="text-sm text-rev">あなたはこの下書きの作者です。承認すると自己承認として監査と画面に印が残ります。</p>
      )}
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>改訂 {draft.revision} を承認しますか？</DialogTitle>
            <DialogDescription>
              承認すると新しい bundle が作られ、以後の配信と検査に使われます。照合する content_hash:{' '}
              <span className="font-mono break-all">{draft.content_hash}</span>
            </DialogDescription>
          </DialogHeader>
          {state.selfApproval && <p className="text-sm text-rev">作者本人の承認（自己承認）として記録されます。</p>}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="secondary">やめる</Button>
            </DialogClose>
            <Button onClick={approve}>承認して有効にする</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  )
}
