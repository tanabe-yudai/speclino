import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { issueMcpToken, listMcpTokens, revokeMcpToken } from '../api/mcpTokens'
import { ApiError } from '../api/errors'
import { Badge } from '../components/Badge'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { formatDateTime } from '../services/format'
import { MCP_TOKEN_STATUS_LABELS, MCP_TOKEN_STATUS_TONE, mcpTokenStatus } from '../services/mcpTokens'
import type { IssuedMcpToken, McpTokenInfo } from '../types/mcpToken'

/** ワークスペースの owner が MCP トークンを発行・失効する画面（S54）。 */
export function McpTokens() {
  const { slug } = useParams<{ slug: string }>()
  if (!slug) {
    return <ErrorState message="ワークスペースが指定されていません" />
  }
  return <McpTokensView key={slug} slug={slug} />
}

function McpTokensView({ slug }: { slug: string }) {
  const [tokens, setTokens] = useState<McpTokenInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [issuing, setIssuing] = useState(false)
  const [issueError, setIssueError] = useState<string | null>(null)
  // 発行直後だけ手に入る平文。この画面を離れる（一覧を再取得する）と二度と見えない
  const [justIssued, setJustIssued] = useState<IssuedMcpToken | null>(null)
  const [copied, setCopied] = useState(false)

  const fetchTokens = useCallback(() => {
    listMcpTokens(slug)
      .then(setTokens)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : 'トークン一覧の取得に失敗しました')
      })
  }, [slug])

  useEffect(() => {
    fetchTokens()
  }, [fetchTokens])

  function retry() {
    setTokens(null)
    setError(null)
    fetchTokens()
  }

  function handleIssue(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!name.trim()) return
    setIssuing(true)
    setIssueError(null)
    issueMcpToken(slug, name.trim())
      .then((issued) => {
        setJustIssued(issued)
        setCopied(false)
        setName('')
        fetchTokens()
      })
      .catch((err: unknown) => {
        setIssueError(err instanceof ApiError ? err.message : 'トークンの発行に失敗しました')
      })
      .finally(() => setIssuing(false))
  }

  function handleRevoke(token: McpTokenInfo) {
    if (!window.confirm(`「${token.name}」を失効させます。以後このトークンでは MCP に接続できなくなります。よろしいですか？`)) {
      return
    }
    revokeMcpToken(slug, token.id)
      .then(fetchTokens)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : 'トークンの失効に失敗しました')
      })
  }

  function copyToken() {
    if (!justIssued) return
    navigator.clipboard
      .writeText(justIssued.token)
      .then(() => setCopied(true))
      .catch(() => setCopied(false))
  }

  return (
    <main className="mcp-tokens-page">
      <Link to="/" className="back-link">
        &larr; run 一覧へ戻る
      </Link>
      <h1>MCP トークン（{slug}）</h1>
      <p className="muted">
        エディタのエージェントが SpecLino の MCP サーバーに接続するためのトークンです。owner だけが
        発行・失効できます。
      </p>

      {justIssued && (
        <div className="state-panel mcp-token-reveal">
          <p>
            <strong>「{justIssued.name}」を発行しました。</strong>
            この画面を閉じると平文は二度と表示されません。今すぐエディタの MCP 設定にコピーしてください。
          </p>
          <div className="mcp-token-value">
            <code>{justIssued.token}</code>
            <button type="button" className="button-secondary" onClick={copyToken}>
              {copied ? 'コピーしました' : 'コピー'}
            </button>
          </div>
        </div>
      )}

      <form className="mcp-token-issue-form" onSubmit={handleIssue}>
        <label>
          トークン名
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例: エディタ（自分の PC）"
            maxLength={100}
            required
          />
        </label>
        <button type="submit" className="button-primary" disabled={issuing || !name.trim()}>
          {issuing ? '発行しています…' : 'トークンを発行'}
        </button>
        {issueError && <span className="mcp-token-issue-error">{issueError}</span>}
      </form>

      {tokens === null && error === null && <LoadingState message="トークン一覧を取得しています…" />}
      {error && <ErrorState message={error} onRetry={retry} />}

      {tokens !== null && tokens.length === 0 && (
        <p className="muted">まだトークンがありません。</p>
      )}

      {tokens !== null && tokens.length > 0 && (
        <table className="run-table">
          <thead>
            <tr>
              <th>名前</th>
              <th>作成者</th>
              <th>作成日時</th>
              <th>最終利用</th>
              <th>状態</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => {
              const status = mcpTokenStatus(token)
              return (
                <tr key={token.id}>
                  <td>{token.name}</td>
                  <td>{token.created_by}</td>
                  <td>{formatDateTime(token.created_at)}</td>
                  <td>{formatDateTime(token.last_used_at)}</td>
                  <td>
                    <Badge tone={MCP_TOKEN_STATUS_TONE[status]}>
                      {MCP_TOKEN_STATUS_LABELS[status]}
                    </Badge>
                  </td>
                  <td>
                    {status === 'active' && (
                      <button
                        type="button"
                        className="button-secondary"
                        onClick={() => handleRevoke(token)}
                      >
                        失効
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </main>
  )
}
