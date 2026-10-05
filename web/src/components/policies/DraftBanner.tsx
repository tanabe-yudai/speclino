import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { approveDraft } from '@/api/policies'
import { Panel } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { POLICY_LEVEL_LABELS, RULE_FIELD_LABELS } from '@/services/labels'
import { diffPackages } from '@/services/policyDraft'
import type { PoliciesView, RuleChange } from '@/types/policy'

const KIND_LABELS: Record<RuleChange['kind'], string> = { added: '追加', removed: '削除', changed: '変更' }

/** 差分 1 件の説明。レベルの変更は「助言 → 必須」のように前後を出す。 */
function describe(change: RuleChange, view: PoliciesView): string {
  if (change.kind !== 'changed') return KIND_LABELS[change.kind]
  return change.fields
    .map((f) => {
      if (f === 'verifier') {
        // 検査器の設定（params）の前後も見せる（画面では編集しないが、API からは変えられる）
        const before = view.active?.package.rules.find((r) => r.id === change.ruleId)?.verifier ?? null
        const after = view.draft?.package.rules.find((r) => r.id === change.ruleId)?.verifier ?? null
        return `${RULE_FIELD_LABELS.verifier}: ${JSON.stringify(before)} → ${JSON.stringify(after)}`
      }
      if (f !== 'level') return RULE_FIELD_LABELS[f] ?? f
      const before = view.active?.package.rules.find((r) => r.id === change.ruleId)?.level
      const after = view.draft?.package.rules.find((r) => r.id === change.ruleId)?.level
      return before && after ? `${POLICY_LEVEL_LABELS[before]} → ${POLICY_LEVEL_LABELS[after]}` : RULE_FIELD_LABELS[f]
    })
    .join('・')
}

/** 下書きがあるときだけ出す帯（design 6 章）。承認のボタンは owner / approver にだけ出す（API も拒否する）。 */
export function DraftBanner({ slug, view, onChanged }: { slug: string; view: PoliciesView; onChanged: () => void }) {
  const [showDiff, setShowDiff] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const draft = view.draft
  if (!draft) return null
  const changes = diffPackages(view.active?.package ?? null, draft.package)

  function approve() {
    if (!draft || !view.draft_hash) return
    setConfirming(false)
    approveDraft(slug, draft.revision, view.draft_hash)
      .then(() => {
        toast.success(`改訂 ${draft.revision} を有効にしました`)
        onChanged()
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 409) {
          toast.error('下書きが変わりました。読み直してから承認してください')
          onChanged()
        } else if (err instanceof ApiError && err.status === 403) {
          toast.error('承認できるのは owner / approver だけです')
        } else {
          toast.error(err instanceof ApiError ? err.message : '承認に失敗しました')
        }
      })
  }

  return (
    <Panel className="space-y-3 border-rev/40 bg-rev-bg/40">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <ToneBadge tone="rev">下書き 改訂 {draft.revision}</ToneBadge>
        <span className="text-sm text-ink-2">
          変更 {changes.length} 件 · 作成 <span className="font-mono text-xs break-all">{draft.author}</span>
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => setShowDiff(!showDiff)} aria-expanded={showDiff}>
            {showDiff ? '差分を閉じる' : '差分を見る'}
          </Button>
          {view.can_approve && (
            <Button size="sm" onClick={() => setConfirming(true)} disabled={!view.draft_hash}>
              承認して有効にする
            </Button>
          )}
        </div>
      </div>
      {draft.note && <p className="text-sm text-ink-2 break-words">{draft.note}</p>}
      {showDiff && (
        <ul className="space-y-1 text-sm">
          {changes.length === 0 && <li className="text-ink-3">有効な改訂との違いはありません</li>}
          {changes.map((c) => (
            <li key={c.ruleId} className="flex flex-wrap gap-x-2">
              <code className="font-mono text-xs break-all">{c.ruleId}</code>
              <span className="text-ink-2">{describe(c, view)}</span>
            </li>
          ))}
        </ul>
      )}
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>改訂 {draft.revision} を有効にしますか？</DialogTitle>
            <DialogDescription>以後の検査はこの改訂で判定します。過去の判定は変わりません。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="secondary">やめる</Button>
            </DialogClose>
            <Button onClick={approve}>承認して有効にする</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {(view.draft_warnings ?? []).length > 0 && (
        <ul role="alert" className="list-disc space-y-1 pl-5 text-sm text-bad">
          {(view.draft_warnings ?? []).map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
