import { Copy } from 'lucide-react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { issueSyncToken, listSyncTokens, revokeSyncToken } from '@/api/syncTokens'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { formatDateTime } from '@/services/format'
import type { IssuedSyncToken, SyncTokenInfo } from '@/types/syncToken'

/**
 * `speclino sync` が承認済みの知識を取りに来るためのトークン（S68）。リポジトリ（プロジェクト）ごとに発行する。
 * 一覧はメンバー全員、発行・失効は owner / approver（API も同じ）。
 */
export function SyncTokensTab({ slug, canManage }: { slug: string; canManage: boolean }) {
  const { projects, error: projectsError } = useWorkspaceProjects(slug)
  const [tokens, setTokens] = useState<SyncTokenInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [projectId, setProjectId] = useState('')
  const [issuing, setIssuing] = useState(false)
  const [justIssued, setJustIssued] = useState<IssuedSyncToken | null>(null)
  const [revoking, setRevoking] = useState<SyncTokenInfo | null>(null)

  const fetchTokens = useCallback(() => {
    listSyncTokens(slug)
      .then(setTokens)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : 'リポジトリ同期トークンの一覧を取得できませんでした'))
  }, [slug])

  useEffect(() => {
    fetchTokens()
  }, [fetchTokens])

  function retry() {
    setError(null)
    setTokens(null)
    fetchTokens()
  }

  function handleIssue(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!name.trim() || !projectId) return
    setIssuing(true)
    issueSyncToken(slug, name.trim(), projectId)
      .then((issued) => {
        setJustIssued(issued)
        setName('')
        fetchTokens()
      })
      .catch((err: unknown) => toast.error(err instanceof ApiError ? err.message : 'リポジトリ同期トークンを発行できませんでした'))
      .finally(() => setIssuing(false))
  }

  function confirmRevoke() {
    const target = revoking
    if (!target) return
    setRevoking(null)
    revokeSyncToken(slug, target.id)
      .then(() => {
        toast.success(`「${target.name}」を失効させました`)
        fetchTokens()
      })
      .catch((err: unknown) => toast.error(err instanceof ApiError ? err.message : 'リポジトリ同期トークンを失効できませんでした'))
  }

  async function copyToken() {
    if (!justIssued) return
    try {
      await navigator.clipboard.writeText(justIssued.token)
      toast.success('コピーしました')
    } catch {
      toast.error('コピーできませんでした。選択してコピーしてください')
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-ink-2">
        リポジトリへ、承認済みの仕様・スキル・指示書を書き出すためのトークンです。
        {canManage ? 'リポジトリごとに発行します。' : '発行と失効はオーナーと承認者が行います。'}
      </p>

      {justIssued && (
        <Panel className="border-primary/30">
          <PanelTitle>「{justIssued.name}」を発行しました（{justIssued.repo_full_name}）</PanelTitle>
          <p className="mt-1 text-sm text-ink-2">この画面を離れると平文は二度と表示されません。今すぐ CI やリポジトリの設定にコピーしてください。</p>
          <div className="mt-3 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-md bg-muted px-3 py-2 font-mono text-xs select-all" aria-label="発行したトークン">
              {justIssued.token}
            </code>
            <Button variant="secondary" size="sm" onClick={copyToken}>
              <Copy className="size-3.5" /> コピー
            </Button>
          </div>
        </Panel>
      )}

      {canManage && (
        <Panel>
          <PanelTitle>リポジトリ同期トークンを発行</PanelTitle>
          <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={handleIssue}>
            <div className="grid min-w-[14rem] flex-1 gap-1.5">
              <Label htmlFor="sync-token-name">トークン名</Label>
              <Input id="sync-token-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="例: CI（main）" maxLength={100} required />
            </div>
            <div className="grid min-w-[14rem] flex-1 gap-1.5">
              <Label htmlFor="sync-token-project">リポジトリ</Label>
              <select
                id="sync-token-project"
                className="h-9 rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                required
                aria-describedby={projectsError ? 'sync-token-project-error' : undefined}
              >
                <option value="">選んでください</option>
                {(projects ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.repo_full_name}
                  </option>
                ))}
              </select>
              {projectsError && (
                <p id="sync-token-project-error" className="text-sm text-bad">
                  {projectsError}
                </p>
              )}
            </div>
            <Button type="submit" disabled={issuing || !name.trim() || !projectId} className="bg-brand text-white">
              {issuing ? '発行しています…' : '発行する'}
            </Button>
          </form>
        </Panel>
      )}

      {tokens === null && error === null && <LoadingState message="リポジトリ同期トークンの一覧を取得しています…" />}
      {error && <ErrorState message={error} onRetry={retry} />}
      {tokens !== null && tokens.length === 0 && <p className="text-sm text-ink-3">まだ リポジトリ同期トークンがありません。</p>}
      {tokens !== null && tokens.length > 0 && (
        <ul className="space-y-2" aria-label="リポジトリ同期トークンの一覧">
          {tokens.map((token) => (
            <li key={token.id} className="row flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-sm">
              <span className="min-w-0 flex-1 truncate font-medium">{token.name}</span>
              <span className="font-mono text-xs break-all text-ink-2">{token.repo_full_name}</span>
              <span className="text-xs text-ink-3">作成 {formatDateTime(token.created_at)}</span>
              <span className="text-xs text-ink-3">最終利用 {formatDateTime(token.last_used_at)}</span>
              {token.revoked_at ? (
                <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-3">失効済み</span>
              ) : (
                <ToneBadge tone="ok">有効</ToneBadge>
              )}
              {canManage && !token.revoked_at && (
                <Button variant="ghost" size="sm" className="text-bad" onClick={() => setRevoking(token)}>
                  失効
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      <Dialog open={revoking !== null} onOpenChange={(open) => !open && setRevoking(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>「{revoking?.name}」を失効させますか？</DialogTitle>
            <DialogDescription>以後このトークンでは内容を取得できなくなります。元には戻せません。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="secondary">やめる</Button>
            </DialogClose>
            <Button variant="destructive" onClick={confirmRevoke}>
              失効させる
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
