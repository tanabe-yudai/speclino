import { ChevronRight } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { createWorkspace } from '@/api/workspaces'
import { PageHeader } from '@/components/app/PageHeader'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { LoadingState } from '@/components/LoadingState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { useMe } from '@/hooks/useMe'
import { WORKSPACE_ROLE_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import { workspaceSlugError } from '@/services/workspaces'

/** 自分のワークスペース（`/workspaces`）: 所属の一覧と、自分がオーナーになるワークスペースの作成。 */
export function Workspaces() {
  const { me, reload } = useMe()
  const navigate = useNavigate()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  useDocumentTitle('自分のワークスペース')

  if (!me) return <LoadingState />

  function handleCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const value = slug.trim()
    const invalid = workspaceSlugError(value)
    if (invalid) {
      setFormError(invalid)
      return
    }
    setCreating(true)
    setFormError(null)
    createWorkspace(value, name.trim() || null)
      .then(async (created) => {
        await reload()
        toast.success(`ワークスペース ${created.name ?? created.slug} を作りました`)
        navigate(paths.workspace(created.slug))
      })
      .catch((err: unknown) => {
        setFormError(err instanceof ApiError ? err.message : 'ワークスペースを作れませんでした')
        setCreating(false)
      })
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader eyebrow="Workspaces" title="自分のワークスペース">
        所属しているワークスペースを選んで開きます。新しく作ると、あなたがオーナーになります。
      </PageHeader>

      {me.workspaces.length > 0 ? (
        <ul className="space-y-2" aria-label="所属しているワークスペース">
          {me.workspaces.map((w) => (
            <li key={w.slug}>
              <Link to={paths.workspace(w.slug)} className="row flex items-center gap-3 px-4 py-3 text-sm">
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">{w.name ?? w.slug}</span>
                  <span className="block truncate font-mono text-xs text-ink-3">{w.slug}</span>
                </span>
                <span className="shrink-0 text-xs text-ink-2">{WORKSPACE_ROLE_LABELS[w.role]}</span>
                <ChevronRight className="size-4 shrink-0 text-ink-3" />
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <Panel>
          <PanelTitle>所属しているワークスペースがありません</PanelTitle>
          <p className="mt-2 text-sm text-ink-2">
            招待リンクを受け取っている場合は、そのリンクを開いてください。下で自分のワークスペースを作ることもできます。
          </p>
        </Panel>
      )}

      <Panel>
        <PanelTitle>ワークスペースを作る</PanelTitle>
        <form className="mt-3 grid gap-4 sm:grid-cols-2" onSubmit={handleCreate}>
          <div className="grid content-start gap-1.5">
            <Label htmlFor="workspace-slug">識別子</Label>
            <Input
              id="workspace-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="例: my-team"
              autoComplete="off"
            />
            <p className="text-xs text-ink-3">英小文字・数字・ハイフン。URL に使い、後から変えられません。</p>
          </div>
          <div className="grid content-start gap-1.5">
            <Label htmlFor="workspace-name">表示名（任意）</Label>
            <Input
              id="workspace-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例: 決済チーム"
              autoComplete="off"
            />
          </div>
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
            <Button type="submit" disabled={creating} className="bg-brand text-white">
              {creating ? '作っています…' : '作る'}
            </Button>
            {formError && <p className="text-sm text-bad">{formError}</p>}
          </div>
        </form>
        <p className="mt-3 text-xs text-ink-3">
          作ったワークスペースは最初は空です。仕様・スキル・指示書・規約を作って承認できます。リポジトリの検査には、設定の「リポジトリ」から
          GitHub のリポジトリを登録します。
        </p>
      </Panel>
    </div>
  )
}
