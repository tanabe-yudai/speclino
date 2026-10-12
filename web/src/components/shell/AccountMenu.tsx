import { Check } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useAuth } from '@/auth/useAuth'
import { useTheme } from '@/components/theme/useTheme'
import { useMe } from '@/hooks/useMe'
import { PROFILE_LABELS, WORKSPACE_ROLE_LABELS } from '@/services/labels'
import type { ThemeChoice } from '@/types/theme'
import { ProfileDialog } from './ProfileDialog'

const THEME_LABELS: Record<ThemeChoice, string> = { system: 'OS に合わせる', light: 'ライト', dark: 'ダーク' }

/** ログイン中のアカウント（表示名とメール）・表示名の変更・所属・テーマ・ログアウト。 */
export function AccountMenu() {
  const { user, signOut } = useAuth()
  const { me, reload } = useMe()
  const { choice, setChoice } = useTheme()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const email = me?.email ?? user?.email ?? null
  const displayName = me?.display_name ?? null
  const initials = Array.from(displayName ?? email ?? '?').slice(0, 2).join('').toUpperCase()

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label="アカウント"
          className="grid size-8 place-items-center rounded-full bg-brand text-xs font-bold text-white shadow-soft"
        >
          {initials}
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuLabel className="grid gap-0.5 font-normal">
            {displayName && <span className="truncate">{displayName}</span>}
            <span className="truncate text-ink-2">{email ?? 'メールアドレスなし'}</span>
          </DropdownMenuLabel>
          {me && <DropdownMenuItem onSelect={() => setEditing(true)}>{PROFILE_LABELS.editDisplayName}</DropdownMenuItem>}
          <DropdownMenuSeparator />
          {me && me.workspaces.length > 0 ? (
            me.workspaces.map((w) => (
              <DropdownMenuLabel key={w.slug} className="flex items-center gap-2 font-normal">
                <span className="truncate">{w.name ?? w.slug}</span>
                <span className="ml-auto text-xs text-ink-3">{WORKSPACE_ROLE_LABELS[w.role]}</span>
              </DropdownMenuLabel>
            ))
          ) : (
            <DropdownMenuLabel className="text-xs font-normal text-ink-3">
              所属しているワークスペースがありません。管理者に招待してもらってください
            </DropdownMenuLabel>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="text-xs text-ink-3">テーマ</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={choice} onValueChange={(v) => setChoice(v as ThemeChoice)}>
            {(Object.keys(THEME_LABELS) as ThemeChoice[]).map((c) => (
              <DropdownMenuRadioItem key={c} value={c}>
                {THEME_LABELS[c]}
                {choice === c && <Check className="sr-only" />}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={async () => {
              await signOut()
              navigate('/login', { replace: true })
            }}
          >
            ログアウト
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ProfileDialog open={editing} current={displayName} onOpenChange={setEditing} onSaved={() => void reload()} />
    </>
  )
}
