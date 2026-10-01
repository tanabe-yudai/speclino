import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { getPolicies, listPolicyAudit, listRevisions, saveDraft } from '@/api/policies'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { AuditLog } from '@/components/policies/AuditLog'
import { DraftBanner } from '@/components/policies/DraftBanner'
import { RevisionHistory } from '@/components/policies/RevisionHistory'
import { RuleEditor } from '@/components/policies/RuleEditor'
import { RuleList } from '@/components/policies/RuleList'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { paths } from '@/services/paths'
import { baseForDraft, removeRule, upsertRule } from '@/services/policyDraft'
import type { AuditEntry, PoliciesView, PolicyRule, RevisionSummary } from '@/types/policy'

interface Loaded {
  view: PoliciesView
  revisions: RevisionSummary[]
  audit: AuditEntry[]
}

/** チーム規約（S43、design 6 章）。左に下書きの帯とルール、右に改訂の履歴と監査ログ。 */
export function Policies() {
  const workspace = useCurrentWorkspace()
  if (!workspace) {
    return (
      <Panel className="mx-auto max-w-xl text-center">
        <PanelTitle className="text-base">このワークスペースは見つかりません</PanelTitle>
        <Link className="mt-3 inline-block text-sm text-primary underline-offset-4 hover:underline" to={paths.home()}>
          ホームへ戻る
        </Link>
      </Panel>
    )
  }
  return <PoliciesBody key={workspace.slug} slug={workspace.slug} name={workspace.name ?? workspace.slug} />
}

function PoliciesBody({ slug, name }: { slug: string; name: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  // undefined = 編集していない、null = 追加、PolicyRule = そのルールを編集
  const [editing, setEditing] = useState<PolicyRule | null | undefined>(undefined)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    Promise.all([getPolicies(slug), listRevisions(slug), listPolicyAudit(slug)])
      .then(([view, revisions, audit]) => alive && setLoaded({ view, revisions, audit }))
      .catch((err: unknown) => {
        if (alive) setError(err instanceof ApiError ? err.message : 'チーム規約を取得できませんでした')
      })
    return () => {
      alive = false
    }
  }, [slug, attempt])

  const reload = useCallback(() => {
    setError(null)
    setAttempt((n) => n + 1)
  }, [])

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (!loaded) return <LoadingState message="チーム規約を取得しています…" />

  const { view } = loaded
  const pkg = baseForDraft(view)

  // 保存の前提は画面に出している下書き（無ければ null）。他の人が先に変えていれば 409
  function persist(next: typeof pkg, note: string) {
    setSaving(true)
    saveDraft(slug, next, note, view.draft?.revision ?? null, view.draft_hash)
      .then(() => {
        toast.success('下書きに保存しました')
        setEditing(undefined)
        reload()
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 409) {
          toast.error('下書きが他の人に変えられました。読み直してから、もう一度変更してください')
          setEditing(undefined)
          reload()
          return
        }
        toast.error(err instanceof ApiError ? err.message : '下書きを保存できませんでした')
      })
      .finally(() => setSaving(false))
  }

  function save(rule: PolicyRule, originalId: string | undefined) {
    persist(upsertRule(pkg, rule, originalId), `${rule.id} を${originalId ? '変更' : '追加'}`)
  }

  function remove(ruleId: string) {
    persist(removeRule(pkg, ruleId), `${ruleId} を削除`)
  }

  return (
    <div className="space-y-6">
      <header className="min-w-0">
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Policies</p>
        <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{name} のチーム規約</h1>
        <p className="mt-1 text-sm text-ink-2">
          {view.active ? `有効な改訂 ${view.active.revision}` : '有効な改訂はまだありません'}
          。ルールの変更は下書きに入り、owner / approver が承認すると以後の検査に使われます。
        </p>
      </header>
      {!view.active && !view.draft && (
        <Panel className="text-sm text-ink-2">
          チーム規約はまだありません。ルールを追加して下書きを作り、owner / approver が承認すると有効になります。
        </Panel>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-6">
          <DraftBanner slug={slug} view={view} onChanged={reload} />
          {editing !== undefined ? (
            <RuleEditor
              rule={editing}
              existingIds={pkg.rules.map((r) => r.id)}
              verifiers={view.verifiers}
              saving={saving}
              onCancel={() => setEditing(undefined)}
              onSave={save}
              onDelete={remove}
            />
          ) : (
            <RuleList view={view} pkg={pkg} onEdit={setEditing} />
          )}
        </div>
        <aside className="min-w-0 space-y-6">
          <RevisionHistory items={loaded.revisions} />
          <AuditLog items={loaded.audit} />
        </aside>
      </div>
    </div>
  )
}
