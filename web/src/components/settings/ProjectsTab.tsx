import { Check, CircleDashed, CircleHelp } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { getOnboarding, getProjectSettings, updateProjectSettings } from '@/api/projectSettings'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { VERDICT_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import {
  ONBOARDING_STATE_LABELS,
  ONBOARDING_STEP_TITLES,
  approverLabel,
  draftSettings,
  needsResave,
  onboardingDone,
  settingsEqual,
} from '@/services/projectSettings'
import type { WorkspaceRole } from '@/types/me'
import type { OnboardingStep, ProjectOnboarding, ProjectSettingsView } from '@/types/projectSettings'

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback)

const STEP_ICON = { done: Check, todo: CircleDashed, unknown: CircleHelp } as const

function Steps({ steps }: { steps: OnboardingStep[] }) {
  return (
    <ol className="space-y-3">
      {steps.map((step, i) => {
        const Icon = STEP_ICON[step.state]
        return (
          <li key={step.id} className="flex gap-3">
            <Icon
              aria-hidden
              className={step.state === 'done' ? 'mt-0.5 size-4 shrink-0 text-ok' : 'mt-0.5 size-4 shrink-0 text-ink-3'}
            />
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                <span>
                  {i + 1}. {ONBOARDING_STEP_TITLES[step.id]}
                </span>
                {step.state === 'done' ? (
                  <ToneBadge tone="ok">{ONBOARDING_STATE_LABELS.done}</ToneBadge>
                ) : (
                  <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-2">
                    {ONBOARDING_STATE_LABELS[step.state]}
                  </span>
                )}
              </p>
              <p className="mt-0.5 text-xs break-words text-ink-3">{step.detail}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** 1 project の設定。owner / approver だけが保存できる（API も 403 で拒否する）。 */
function SettingsForm({ slug, projectId }: { slug: string; projectId: string }) {
  const [view, setView] = useState<ProjectSettingsView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [agentEnabled, setAgentEnabled] = useState(true)
  const [testRunEnabled, setTestRunEnabled] = useState(true)
  const [approversText, setApproversText] = useState('')
  const [saving, setSaving] = useState(false)

  const apply = useCallback((v: ProjectSettingsView) => {
    setView(v)
    setAgentEnabled(v.settings.agent_enabled)
    setTestRunEnabled(v.settings.test_run_enabled)
    setApproversText(v.settings.approvers.join(', '))
  }, [])

  const fetchSettings = useCallback(() => {
    getProjectSettings(slug, projectId)
      .then(apply)
      .catch((err: unknown) => setError(message(err, '設定を取得できませんでした')))
  }, [slug, projectId, apply])

  useEffect(() => {
    fetchSettings()
  }, [fetchSettings])

  if (error) {
    return (
      <ErrorState
        message={error}
        onRetry={() => {
          setError(null)
          fetchSettings()
        }}
      />
    )
  }
  if (!view) return <LoadingState message="設定を取得しています…" />

  const { settings: draft, error: approversError } = draftSettings(view, {
    agentEnabled,
    testRunEnabled,
    approversText,
  })
  const dirty = !settingsEqual(draft, view.settings) || needsResave(view)
  const editable = view.can_edit

  function save() {
    if (approversError) return
    setSaving(true)
    updateProjectSettings(slug, projectId, draft)
      .then((v) => {
        apply(v)
        toast.success('設定を保存しました')
      })
      .catch((err: unknown) => toast.error(message(err, '設定を保存できませんでした')))
      .finally(() => setSaving(false))
  }

  return (
    <div className="space-y-4">
      {view.stored_invalid && (
        <p className="rounded-md bg-rev-bg px-3 py-2 text-sm text-rev">
          保存されている設定が読めないため、エージェントと test_run を止めた安全側の設定で動いています。保存し直してください。
        </p>
      )}
      {view.approver_ids_missing && (
        <p className="rounded-md bg-rev-bg px-3 py-2 text-sm text-rev">
          承認者の GitHub ユーザー ID が未登録です。保存し直してください
        </p>
      )}
      <div className="space-y-1">
        <p className="text-sm font-medium">ゲートを失敗にする判定</p>
        <p className="text-sm text-ink-2">
          {view.settings.fail_on.map((v) => VERDICT_LABELS[v]).join('・')}
          <span className="ml-1 text-xs text-ink-3">（フェイルクローズのため外せません）</span>
        </p>
      </div>
      <fieldset className="space-y-2" disabled={!editable}>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={agentEnabled}
            onChange={(e) => setAgentEnabled(e.target.checked)}
          />
          <span>
            エージェントで証拠を集める
            <span className="block text-xs text-ink-3">止めると、未検証の要求は未検証のまま残ります（判定はしません）。</span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={testRunEnabled}
            onChange={(e) => setTestRunEnabled(e.target.checked)}
          />
          <span>
            宣言されたテストを隔離環境で実行する（test_run）
            <span className="block text-xs text-ink-3">サーバー全体で無効なときは、ここで有効にしても動きません。</span>
          </span>
        </label>
        <div className="space-y-1.5 pt-1">
          {view.can_edit_approvers ? (
            <>
              <Label htmlFor={`approvers-${projectId}`}>仕様の承認者（GitHub のログイン名）</Label>
              <Textarea
                id={`approvers-${projectId}`}
                rows={2}
                className="font-mono text-xs"
                value={approversText}
                onChange={(e) => setApproversText(e.target.value)}
                placeholder="例: octo-cat, reviewer-1"
              />
            </>
          ) : (
            <p className="text-sm font-medium">仕様の承認者（GitHub のログイン名）</p>
          )}
          {view.approvers.length > 0 ? (
            <>
              <ul className="font-mono text-xs break-words text-ink-2">
                {view.approvers.map((a) => (
                  <li key={a.login}>{approverLabel(a)}</li>
                ))}
              </ul>
              <p className="text-xs text-ink-3">ログイン名を他人が取得していないか、ID を確かめてください</p>
            </>
          ) : (
            !view.can_edit_approvers && <p className="font-mono text-xs text-ink-2">（未設定）</p>
          )}
          <p className="text-xs text-ink-3">
            PR コメントの /speclino approve-spec・answer・attest を受け付ける人。空なら誰も承認できません。変更できるのはオーナーだけです。
          </p>
          {approversError && <p className="text-sm text-bad">{approversError}</p>}
        </div>
      </fieldset>
      {editable ? (
        <Button onClick={save} disabled={saving || !dirty || approversError !== null} className="bg-brand text-white">
          {saving ? '保存しています…' : '保存する'}
        </Button>
      ) : (
        <p className="text-xs text-ink-3">設定を変更できるのはオーナーと承認者だけです。</p>
      )}
    </div>
  )
}

function ProjectCard({ slug, project }: { slug: string; project: ProjectOnboarding }) {
  const done = onboardingDone(project.steps)
  return (
    <Panel className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <PanelTitle className="min-w-0 flex-1 truncate font-mono text-sm">{project.repo_full_name}</PanelTitle>
        <Link className="text-xs text-primary underline-offset-4 hover:underline" to={paths.project(slug, project.repo_full_name)}>
          プロジェクトを開く
        </Link>
      </div>
      <section className="space-y-3">
        <h3 className="text-xs font-semibold tracking-wide text-ink-3">{done ? 'はじめ方（完了）' : 'はじめ方'}</h3>
        <Steps steps={project.steps} />
      </section>
      <section className="space-y-3 border-t pt-4">
        <h3 className="text-xs font-semibold tracking-wide text-ink-3">設定</h3>
        <SettingsForm slug={slug} projectId={project.project_id} />
      </section>
    </Panel>
  )
}

/** プロジェクトごとのはじめ方（到達状態）と設定（S47）。 */
export function ProjectsTab({ slug, role }: { slug: string; role: WorkspaceRole | null }) {
  const [projects, setProjects] = useState<ProjectOnboarding[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchOnboarding = useCallback(() => {
    getOnboarding(slug)
      .then((o) => setProjects(o.projects))
      .catch((err: unknown) => setError(message(err, 'プロジェクトの状態を取得できませんでした')))
  }, [slug])

  useEffect(() => {
    fetchOnboarding()
  }, [fetchOnboarding])

  return (
    <div className="space-y-6">
      <p className="text-sm text-ink-2">
        GitHub App を入れてリポジトリを登録し、<code className="font-mono text-xs">.speclino/</code>{' '}
        と承認済みの仕様を置いて PR を開くと、SpecLino が検査を始めます。各段階は SpecLino が持っている記録
        （登録・検査の結果）から表示し、確かめられない段階は「未確認」と出します。
      </p>
      {error && (
        <ErrorState
          message={error}
          onRetry={() => {
            setError(null)
            setProjects(null)
            fetchOnboarding()
          }}
        />
      )}
      {!error && projects === null && <LoadingState message="プロジェクトを取得しています…" />}
      {projects !== null && projects.length === 0 && (
        <Panel className="text-sm text-ink-2">
          まだリポジトリが登録されていません。
          {role === 'owner' ? (
            <>
              {' '}
              <Link className="text-primary underline-offset-4 hover:underline" to={paths.settings(slug, 'repositories')}>
                リポジトリを追加する
              </Link>
            </>
          ) : (
            ' オーナーにリポジトリの登録を依頼してください。'
          )}
        </Panel>
      )}
      {projects?.map((p) => (
        <ProjectCard key={p.project_id} slug={slug} project={p} />
      ))}
    </div>
  )
}
