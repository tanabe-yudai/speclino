import { useEffect, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { acceptInvitation, previewInvitation } from '@/api/members'
import { ApiError } from '@/api/errors'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { LoadingState } from '@/components/LoadingState'
import { Button } from '@/components/ui/button'
import { useMe } from '@/hooks/useMe'
import { formatDateTime } from '@/services/format'
import { WORKSPACE_ROLE_LABELS } from '@/services/labels'
import { acceptBlocker, tokenFromHash } from '@/services/members'
import { paths } from '@/services/paths'
import type { InvitationPreview } from '@/types/member'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

type Loaded = { token: string; preview: InvitationPreview | null; error: string | null }

function Message({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Panel className="mx-auto max-w-xl text-center">
      <PanelTitle className="text-base">{title}</PanelTitle>
      {children}
      <Link className="mt-3 inline-block text-sm text-primary underline-offset-4 hover:underline" to={paths.home()}>
        ホームへ戻る
      </Link>
    </Panel>
  )
}

/** 招待リンク（`/invite#<token>`）を開いた人が、内容を確かめて受諾する（S56）。 */
export function AcceptInvite() {
  const { hash } = useLocation()
  const navigate = useNavigate()
  const { me, reload } = useMe()
  const token = tokenFromHash(hash)
  useDocumentTitle('招待')
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [accepting, setAccepting] = useState(false)
  const [acceptError, setAcceptError] = useState<string | null>(null)

  useEffect(() => {
    if (!token) return
    let alive = true
    previewInvitation(token)
      .then((preview) => alive && setLoaded({ token, preview, error: null }))
      .catch((err: unknown) => {
        if (alive) {
          setLoaded({ token, preview: null, error: err instanceof ApiError ? err.message : '招待を確認できませんでした' })
        }
      })
    return () => {
      alive = false
    }
  }, [token])

  if (!token) {
    return (
      <Message title="招待リンクが正しくありません">
        <p className="mt-2 text-sm text-ink-2">受け取ったリンクを、省略せずにそのまま開いてください。</p>
      </Message>
    )
  }
  if (!me || !loaded || loaded.token !== token) return <LoadingState message="招待を確認しています…" />
  if (!loaded.preview) {
    return (
      <Message title="この招待は使えません">
        <p className="mt-2 text-sm text-ink-2">{loaded.error}</p>
        <p className="mt-1 text-sm text-ink-2">
          期限切れ・使用済み・取り消し済みのときは、オーナーに招待し直してもらってください。
        </p>
      </Message>
    )
  }

  const preview = loaded.preview
  const blocker = acceptBlocker(me, preview)
  const workspaceName = preview.name ?? preview.slug

  function accept() {
    if (!token) return
    setAccepting(true)
    setAcceptError(null)
    acceptInvitation(token)
      // 所属を読み直してから移る（古い所属のままだと「見つかりません」が一瞬出る）。
      // replace でトークン入りの URL を履歴に残さない
      .then((result) => reload().then(() => navigate(paths.workspace(result.slug), { replace: true })))
      .catch((err: unknown) => {
        setAcceptError(err instanceof ApiError ? err.message : '受諾できませんでした')
        setAccepting(false)
      })
  }

  return (
    <Panel className="mx-auto max-w-xl space-y-4">
      <div>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Invitation</p>
        <PanelTitle className="mt-1 text-lg break-words">{workspaceName} への招待</PanelTitle>
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="text-ink-3">権限</dt>
        <dd>{WORKSPACE_ROLE_LABELS[preview.role]}</dd>
        <dt className="text-ink-3">宛先</dt>
        <dd className="break-all">{preview.email}</dd>
        <dt className="text-ink-3">ログイン中</dt>
        <dd className="break-all">{me.email ?? '—'}</dd>
        <dt className="text-ink-3">期限</dt>
        <dd>{formatDateTime(preview.expires_at)}</dd>
      </dl>
      {blocker && <p className="rounded-md bg-rev-bg px-3 py-2 text-sm text-rev">{blocker}</p>}
      {acceptError && <p className="text-sm text-bad">{acceptError}</p>}
      <Button onClick={accept} disabled={accepting || blocker !== null} className="w-full bg-brand text-white sm:w-auto">
        {accepting ? '受諾しています…' : '招待を受ける'}
      </Button>
    </Panel>
  )
}
