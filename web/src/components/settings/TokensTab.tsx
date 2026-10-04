import { Copy } from 'lucide-react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import { issueMcpToken, listMcpTokens, revokeMcpToken } from '@/api/mcpTokens'
import { ApiError } from '@/api/errors'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { A2aPanel } from '@/components/settings/A2aPanel'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatDateTime } from '@/services/format'
import { MCP_TOKEN_STATUS_LABELS, mcpTokenStatus } from '@/services/mcpTokens'
import type { IssuedMcpToken, McpTokenInfo } from '@/types/mcpToken'

/** ワークスペースの owner が MCP トークンを発行・失効する（S54）。 */
export function TokensTab({ slug }: { slug: string }) {
  const [tokens, setTokens] = useState<McpTokenInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [issuing, setIssuing] = useState(false)
  // 発行直後だけ手に入る平文。一覧を取り直しても消さず、この画面を離れると二度と見えない
  const [justIssued, setJustIssued] = useState<IssuedMcpToken | null>(null)
  const [revoking, setRevoking] = useState<McpTokenInfo | null>(null)

  const fetchTokens = useCallback(() => {
    listMcpTokens(slug)
      .then(setTokens)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : 'トークン一覧の取得に失敗しました'))
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
    if (!name.trim()) return
    setIssuing(true)
    issueMcpToken(slug, name.trim())
      .then((issued) => {
        setJustIssued(issued)
        setName('')
        fetchTokens()
      })
      .catch((err: unknown) => toast.error(err instanceof ApiError ? err.message : 'トークンの発行に失敗しました'))
      .finally(() => setIssuing(false))
  }

  function confirmRevoke() {
    const target = revoking
    if (!target) return
    setRevoking(null)
    revokeMcpToken(slug, target.id)
      .then(() => {
        toast.success(`「${target.name}」を失効させました`)
        fetchTokens()
      })
      .catch((err: unknown) => toast.error(err instanceof ApiError ? err.message : 'トークンの失効に失敗しました'))
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
        エディタのエージェントが SpecLino の MCP サーバーに接続するためのトークンです。owner だけが発行・失効できます。
      </p>

      {justIssued && (
        <Panel className="border-primary/30">
          <PanelTitle>「{justIssued.name}」を発行しました</PanelTitle>
          <p className="mt-1 text-sm text-ink-2">
            この画面を離れると平文は二度と表示されません。今すぐエディタの MCP 設定にコピーしてください。
          </p>
          <div className="mt-3 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-md bg-muted px-3 py-2 font-mono text-xs select-all">
              {justIssued.token}
            </code>
            <Button variant="secondary" size="sm" onClick={copyToken}>
              <Copy className="size-3.5" /> コピー
            </Button>
          </div>
        </Panel>
      )}

      <Panel>
        <PanelTitle>トークンを発行</PanelTitle>
        <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={handleIssue}>
          <div className="grid min-w-[16rem] flex-1 gap-1.5">
            <Label htmlFor="token-name">トークン名</Label>
            <Input
              id="token-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例: エディタ（自分の PC）"
              maxLength={100}
              required
            />
          </div>
          <Button type="submit" disabled={issuing || !name.trim()} className="bg-brand text-white">
            {issuing ? '発行しています…' : '発行する'}
          </Button>
        </form>
      </Panel>

      {tokens === null && error === null && <LoadingState message="トークン一覧を取得しています…" />}
      {error && <ErrorState message={error} onRetry={retry} />}
      {tokens !== null && tokens.length === 0 && <p className="text-sm text-ink-3">まだトークンがありません。</p>}
      {tokens !== null && tokens.length > 0 && (
        <ul className="space-y-2">
          {tokens.map((token) => {
            const status = mcpTokenStatus(token)
            return (
              <li key={token.id} className="row flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-sm">
                <span className="min-w-0 flex-1 truncate font-medium">{token.name}</span>
                <span className="text-xs text-ink-3">作成 {formatDateTime(token.created_at)}</span>
                <span className="text-xs text-ink-3">最終利用 {formatDateTime(token.last_used_at)}</span>
                {status === 'active' ? (
                  <ToneBadge tone="ok">{MCP_TOKEN_STATUS_LABELS[status]}</ToneBadge>
                ) : (
                  <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-3">
                    {MCP_TOKEN_STATUS_LABELS[status]}
                  </span>
                )}
                {status === 'active' && (
                  <Button variant="ghost" size="sm" className="text-bad" onClick={() => setRevoking(token)}>
                    失効
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      <A2aPanel origin={window.location.origin} />

      <Dialog open={revoking !== null} onOpenChange={(open) => !open && setRevoking(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>「{revoking?.name}」を失効させますか？</DialogTitle>
            <DialogDescription>以後このトークンでは MCP に接続できなくなります。元には戻せません。</DialogDescription>
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
