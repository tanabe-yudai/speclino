import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMe } from '../api/me'
import { useAuth } from '../auth/useAuth'
import { WORKSPACE_ROLE_LABELS } from '../services/labels'
import type { Me } from '../types/me'

/**
 * ログイン後の全画面に出すヘッダー。誰でログインしていて、どのワークスペースに何の role で
 * 所属しているかを見せ、ログアウトできるようにする（別のアカウントで入っていることに気づけるように）。
 * owner のワークスペースには MCP トークンの管理画面へのリンクを出す。
 */
export function AccountBar() {
  const { user, signOut } = useAuth()
  const [me, setMe] = useState<Me | null>(null)

  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null))
  }, [])

  const email = me?.email ?? user?.email ?? user?.uid ?? ''

  return (
    <header className="account-bar">
      <div className="account-bar-identity">
        <span className="account-bar-email">{email}</span>
        {me !== null &&
          (me.workspaces.length === 0 ? (
            <span className="muted">所属ワークスペースなし</span>
          ) : (
            me.workspaces.map((w) => (
              <span key={w.slug} className="account-bar-workspace">
                {w.name ?? w.slug}（{WORKSPACE_ROLE_LABELS[w.role]}）
                {w.role === 'owner' && (
                  <Link to={`/workspaces/${w.slug}/mcp-tokens`}>MCP トークン</Link>
                )}
              </span>
            ))
          ))}
      </div>
      <button type="button" className="button-secondary" onClick={() => void signOut()}>
        ログアウト
      </button>
    </header>
  )
}
