import { Copy } from 'lucide-react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import {
  changeMemberRole,
  issueInvitation,
  listInvitations,
  listMembers,
  removeMember,
  revokeInvitation,
} from '@/api/members'
import { ApiError } from '@/api/errors'
import { Panel, PanelTitle } from '@/components/app/Panel'
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
import { WORKSPACE_ROLE_LABELS } from '@/services/labels'
import {
  INVITATION_STATUS_LABELS,
  canManageMembers,
  invitationLink,
  invitationStatus,
  isLastOwner,
  isValidEmail,
  memberLabel,
  sortMembers,
} from '@/services/members'
import type { WorkspaceRole } from '@/types/me'
import type { Invitation, IssuedInvitation, WorkspaceMember } from '@/types/member'

const ROLES: WorkspaceRole[] = ['owner', 'approver', 'member']
const SELECT_CLASS = 'h-8 rounded-md border bg-card px-2 text-sm disabled:opacity-50'
const ROLE_HELP = 'オーナー: 招待・権限の変更・リポジトリ登録・エージェント接続トークン / 承認者: 規約の承認と設定の変更 / メンバー: 閲覧と検査の開始'

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback)

type Pending = { kind: 'remove'; member: WorkspaceMember } | { kind: 'revoke'; invitation: Invitation }

/** メンバーの一覧（全員）と、owner による招待・role の変更・削除（S56）。 */
export function MembersTab({
  slug,
  role,
  myUid,
  onSelfChanged,
}: {
  slug: string
  role: WorkspaceRole | null
  myUid: string
  /** 自分の role を変えた・自分を外したとき（所属と role を読み直す）。 */
  onSelfChanged: () => void
}) {
  const isOwner = canManageMembers(role)
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null)
  const [invitations, setInvitations] = useState<Invitation[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [email, setEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<WorkspaceRole>('member')
  const [inviting, setInviting] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)
  // 発行直後だけ手に入るリンク。この画面を離れると二度と見えない
  const [justIssued, setJustIssued] = useState<IssuedInvitation | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [busyUid, setBusyUid] = useState<string | null>(null)

  const fetchAll = useCallback(() => {
    listMembers(slug)
      .then(setMembers)
      .catch((err: unknown) => setError(message(err, 'メンバーの一覧を取得できませんでした')))
    if (isOwner) {
      listInvitations(slug)
        .then(setInvitations)
        .catch((err: unknown) => setError(message(err, '招待の一覧を取得できませんでした')))
    }
  }, [slug, isOwner])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  function retry() {
    setError(null)
    setMembers(null)
    setInvitations(null)
    fetchAll()
  }

  function handleInvite(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!isValidEmail(email)) {
      setInviteError('メールアドレスの形で入力してください（例: name@example.com）')
      return
    }
    setInviting(true)
    setInviteError(null)
    issueInvitation(slug, email.trim(), inviteRole)
      .then((issued) => {
        setJustIssued(issued)
        setEmail('')
        fetchAll()
      })
      .catch((err: unknown) => setInviteError(message(err, '招待を作成できませんでした')))
      .finally(() => setInviting(false))
  }

  function handleRoleChange(member: WorkspaceMember, next: WorkspaceRole) {
    setBusyUid(member.uid)
    changeMemberRole(slug, member.uid, next)
      .then(() => {
        toast.success(`${memberLabel(member)} を${WORKSPACE_ROLE_LABELS[next]}にしました`)
        fetchAll()
        if (member.uid === myUid) onSelfChanged()
      })
      .catch((err: unknown) => toast.error(message(err, '権限を変更できませんでした')))
      .finally(() => setBusyUid(null))
  }

  function confirmPending() {
    const target = pending
    if (!target) return
    setPending(null)
    const done =
      target.kind === 'remove'
        ? removeMember(slug, target.member.uid).then(() => `${memberLabel(target.member)} を外しました`)
        : revokeInvitation(slug, target.invitation.id).then(() => `${target.invitation.email} への招待を取り消しました`)
    done
      .then((text) => {
        toast.success(text)
        if (target.kind === 'remove' && target.member.uid === myUid) onSelfChanged()
        else fetchAll()
      })
      .catch((err: unknown) => toast.error(message(err, '操作に失敗しました')))
  }

  async function copyLink() {
    if (!justIssued) return
    try {
      await navigator.clipboard.writeText(invitationLink(window.location.origin, justIssued.token))
      toast.success('コピーしました')
    } catch {
      toast.error('コピーできませんでした。選択してコピーしてください')
    }
  }

  const sorted = members ? sortMembers(members) : null
  const now = new Date()

  return (
    <div className="space-y-6">
      <p className="text-sm text-ink-2">
        ワークスペースのメンバーは SpecLino で管理します（GitHub とは同期しません）。招待・権限の変更・削除はオーナーだけができ、記録が残ります。
      </p>

      {justIssued && (
        <Panel className="border-primary/30">
          <PanelTitle>{justIssued.email} への招待リンクを作成しました</PanelTitle>
          <p className="mt-1 text-sm text-ink-2">
            メールは送信されません。このリンクを相手に送ってください。リンクは {formatDateTime(justIssued.expires_at)}{' '}
            まで 1 回だけ使え、この画面を離れると二度と表示されません。相手は招待されたメールアドレスのアカウント（確認済み）で
            ログインして受諾します。
          </p>
          <div className="mt-3 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-md bg-muted px-3 py-2 font-mono text-xs select-all">
              {invitationLink(window.location.origin, justIssued.token)}
            </code>
            <Button variant="secondary" size="sm" onClick={copyLink}>
              <Copy className="size-3.5" /> コピー
            </Button>
          </div>
        </Panel>
      )}

      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && sorted === null && <LoadingState message="メンバーを取得しています…" />}
      {sorted !== null && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-ink-2">メンバー（{sorted.length}）</h2>
          <ul className="space-y-2">
            {sorted.map((m) => {
              const locked = isLastOwner(m, sorted)
              return (
                <li key={m.uid} className="row flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">
                      {memberLabel(m)}
                      {m.uid === myUid && <span className="ml-2 text-xs text-ink-3">（あなた）</span>}
                    </p>
                  </div>
                  {isOwner ? (
                    <select
                      aria-label={`${memberLabel(m)} の権限`}
                      className={SELECT_CLASS}
                      value={m.role}
                      disabled={busyUid === m.uid}
                      onChange={(e) => handleRoleChange(m, e.target.value as WorkspaceRole)}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r} disabled={locked && r !== 'owner'}>
                          {WORKSPACE_ROLE_LABELS[r]}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-2">{WORKSPACE_ROLE_LABELS[m.role]}</span>
                  )}
                  {isOwner && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-bad"
                      disabled={locked}
                      title={locked ? '最後のオーナーは外せません' : undefined}
                      onClick={() => setPending({ kind: 'remove', member: m })}
                    >
                      外す
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
          <p className="text-xs text-ink-3">{ROLE_HELP}</p>
        </section>
      )}

      {isOwner && (
        <Panel>
          <PanelTitle>メンバーを招待</PanelTitle>
          <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={handleInvite}>
            <div className="grid min-w-[14rem] flex-1 gap-1.5">
              <Label htmlFor="invite-email">メールアドレス</Label>
              <Input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
                autoComplete="off"
                maxLength={254}
                required
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="invite-role">権限</Label>
              <select
                id="invite-role"
                className="h-8 rounded-md border bg-card px-2 text-sm"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as WorkspaceRole)}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {WORKSPACE_ROLE_LABELS[r]}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" disabled={inviting || !email.trim()} className="bg-brand text-white">
              {inviting ? '作成しています…' : '招待リンクを作る'}
            </Button>
          </form>
          {inviteError && <p className="mt-2 text-sm text-bad">{inviteError}</p>}
        </Panel>
      )}

      {isOwner && invitations !== null && invitations.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-ink-2">未使用の招待</h2>
          <ul className="space-y-2">
            {invitations.map((inv) => {
              const status = invitationStatus(inv, now)
              return (
                <li key={inv.id} className="row flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-sm">
                  <span className="min-w-0 flex-1 truncate font-medium">{inv.email}</span>
                  <span className="text-xs text-ink-3">{WORKSPACE_ROLE_LABELS[inv.role]}</span>
                  <span className="text-xs text-ink-3">期限 {formatDateTime(inv.expires_at)}</span>
                  <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-2">
                    {INVITATION_STATUS_LABELS[status]}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-bad"
                    onClick={() => setPending({ kind: 'revoke', invitation: inv })}
                  >
                    取り消す
                  </Button>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pending?.kind === 'remove'
                ? `${memberLabel(pending.member)} をワークスペースから外しますか？`
                : `${pending?.invitation.email ?? ''} への招待を取り消しますか？`}
            </DialogTitle>
            <DialogDescription>
              {pending?.kind === 'remove'
                ? 'このワークスペースのプロジェクト・規約・検査が見えなくなります。もう一度入るには招待が要ります。'
                : 'このリンクでは受諾できなくなります。'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="secondary">やめる</Button>
            </DialogClose>
            <Button variant="destructive" onClick={confirmPending}>
              {pending?.kind === 'remove' ? '外す' : '取り消す'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
