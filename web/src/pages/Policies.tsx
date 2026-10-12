import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { getPolicies, listPolicyAudit, listRevisions, saveDraft } from '@/api/policies'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ErrorState } from '@/components/ErrorState'
import { ChatPanel } from '@/components/knowledge/ChatPanel'
import { NoteField } from '@/components/knowledge/NoteField'
import { LoadingState } from '@/components/LoadingState'
import { AuditLog } from '@/components/policies/AuditLog'
import { DraftBanner } from '@/components/policies/DraftBanner'
import { RevisionHistory } from '@/components/policies/RevisionHistory'
import { RuleEditor } from '@/components/policies/RuleEditor'
import { RuleList } from '@/components/policies/RuleList'
import { Button } from '@/components/ui/button'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useKnowledgeChat } from '@/hooks/useKnowledgeChat'
import { applyRuleProposal, canSummarize, currentRuleText, proposalText, ruleEditorText } from '@/services/knowledgeChat'
import { paths } from '@/services/paths'
import { policiesCrumbs } from '@/services/breadcrumbs'
import { Breadcrumbs } from '@/components/shell/Breadcrumbs'
import { baseForDraft, newRule, removeRule, upsertRule } from '@/services/policyDraft'
import type { NoteState, RawProposal } from '@/types/knowledgeChat'
import type { AuditEntry, PoliciesView, PolicyRule, RevisionSummary } from '@/types/policy'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

interface Loaded {
  view: PoliciesView
  revisions: RevisionSummary[]
  audit: AuditEntry[]
}

/** 規約（チーム規約、S43、design 6 章。`/w/:slug/knowledge/policies`）。左に下書きの帯とルール、右に改訂の履歴と監査ログ。 */
export function Policies() {
  const workspace = useCurrentWorkspace()
  useDocumentTitle('規約')
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
  return <PoliciesBody key={workspace.slug} slug={workspace.slug} />
}

function PoliciesBody({ slug }: { slug: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  // undefined = 編集していない、null = 追加、PolicyRule = そのルールを編集
  const [editing, setEditing] = useState<PolicyRule | null | undefined>(undefined)
  const [saving, setSaving] = useState(false)
  // S73: AI と作る。規約の会話は artifact に結び付かない。提案はルールの編集欄に入れる
  const [chatOpen, setChatOpen] = useState(false)
  const chat = useKnowledgeChat(slug, 'policy', null, null)
  const [ruleDraft, setRuleDraft] = useState<PolicyRule | null>(null)
  const [seed, setSeed] = useState<{ key: number; rule: PolicyRule } | null>(null)
  const [note, setNote] = useState<NoteState>({ text: '', source: 'manual' })

  useEffect(() => {
    let alive = true
    Promise.all([getPolicies(slug), listRevisions(slug), listPolicyAudit(slug)])
      .then(([view, revisions, audit]) => alive && setLoaded({ view, revisions, audit }))
      .catch((err: unknown) => {
        if (alive) setError(err instanceof ApiError ? err.message : '規約を取得できませんでした')
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
  if (!loaded) return <LoadingState message="規約を取得しています…" />

  const { view } = loaded
  const pkg = baseForDraft(view)

  function closeEditor() {
    setEditing(undefined)
    setSeed(null)
    setRuleDraft(null)
    setNote({ text: '', source: 'manual' })
  }

  // 保存の前提は画面に出している下書き（無ければ null）。他の人が先に変えていれば 409
  function persist(next: typeof pkg, autoNote: string) {
    setSaving(true)
    const written = note.text.trim()
    saveDraft(slug, next, written || autoNote, view.draft?.revision ?? null, view.draft_hash, written ? note.source : 'manual')
      .then(() => {
        toast.success('下書きに保存しました')
        closeEditor()
        reload()
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 409) {
          toast.error('下書きが他の人に変えられました。読み直してから、もう一度変更してください')
          closeEditor()
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

  /** 提案を重ねる土台: 編集中のルール、編集していなければ提案と同じ ID の既存のルール（無ければ null）。 */
  function proposalBase(raw: RawProposal): PolicyRule | null {
    if (editing !== undefined) return ruleDraft ?? editing
    return pkg.rules.find((r) => r.id === raw.rule?.id) ?? null
  }

  /** 提案をルールの編集欄に入れる（保存はしない）。編集していなければ、同じ ID のルールか新しいルールを開く。 */
  function applyChatProposal(raw: RawProposal) {
    const base = proposalBase(raw)
    if (editing === undefined) setEditing(base)
    setSeed((s) => ({ key: (s?.key ?? 0) + 1, rule: applyRuleProposal(base ?? newRule(''), raw) }))
    toast.success('提案をルールの編集欄に反映しました（まだ保存していません）')
  }

  const editingRule = editing !== undefined ? ruleDraft : null

  return (
    <div className="space-y-6">
      <header className="min-w-0">
        <Breadcrumbs items={policiesCrumbs(slug)} />
        <h1 className="mt-2 truncate text-2xl font-bold tracking-tight">規約</h1>
        <p className="mt-1 text-sm text-ink-2">
          チームのすべてのプロジェクトに効く規約です。
          {view.active ? `有効な改訂 ${view.active.revision}` : '有効な改訂はまだありません'}
          。ルールの変更は下書きに入り、オーナーか承認者が承認すると、以後の検査に使われます。
        </p>
        <Button type="button" size="sm" className="mt-3" variant={chatOpen ? 'secondary' : 'outline'} onClick={() => setChatOpen(!chatOpen)} aria-expanded={chatOpen}>
          {chatOpen ? 'AI と作るパネルを閉じる' : 'AI と作る'}
        </Button>
      </header>
      {chatOpen && (
        <ChatPanel
          chat={chat}
          editorText={ruleEditorText(editingRule)}
          beforeText={(raw) => currentRuleText(proposalBase(raw))}
          afterText={(raw) => proposalText(raw)}
          onApply={applyChatProposal}
        />
      )}
      {!view.active && !view.draft && (
        <Panel className="text-sm text-ink-2">
          チーム規約はまだありません。ルールを追加して下書きを作り、オーナーか承認者が承認すると有効になります。
        </Panel>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-6">
          <DraftBanner slug={slug} view={view} onChanged={reload} />
          {editing !== undefined ? (
            <RuleEditor
              key={seed?.key ?? 0}
              rule={editing}
              initial={seed?.rule}
              onDraftChange={setRuleDraft}
              existingIds={pkg.rules.map((r) => r.id)}
              verifiers={view.verifiers}
              saving={saving}
              onCancel={closeEditor}
              onSave={save}
              onDelete={remove}
              footer={<NoteField id="policy-note" note={note} onChange={setNote} summarize={canSummarize(chat.current) ? chat.summarize : undefined} />}
            />
          ) : (
            <RuleList
              view={view}
              pkg={pkg}
              onEdit={(r) => {
                setSeed(null)
                setEditing(r)
              }}
            />
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
