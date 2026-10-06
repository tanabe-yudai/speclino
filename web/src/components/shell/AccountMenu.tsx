import { Check, Copy } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
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
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useMe } from '@/hooks/useMe'
import { paths } from '@/services/paths'
import { WORKSPACE_ROLE_LABELS } from '@/services/labels'
import type { ThemeChoice } from '@/types/theme'

const THEME_LABELS: Record<ThemeChoice, string> = { system: 'OS に合わせる', light: 'ライト', dark: 'ダーク' }

/** ログイン中のアカウント・所属・テーマ・ログアウト。 */
export function AccountMenu() {
  const workspace = useCurrentWorkspace()
  const { user, signOut } = useAuth()
  const { me } = useMe()
  const { choice, setChoice } = useTheme()
  const navigate = useNavigate()
  const email = me?.email ?? user?.email ?? null
  const initials = (email ?? me?.uid ?? '?').slice(0, 2).toUpperCase()

  async function copyUid() {
    if (!me) return
    try {
      await navigator.clipboard.writeText(me.uid)
      toast.success('uid をコピーしました')
    } catch {
      toast.error('コピーできませんでした')
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="アカウント"
        className="grid size-8 place-items-center rounded-full bg-brand text-xs font-bold text-white shadow-soft"
      >
        {initials}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="truncate font-normal text-ink-2">{email ?? 'メールアドレスなし'}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {me && me.workspaces.length > 0 ? (
          me.workspaces.map((w) => (
            <DropdownMenuLabel key={w.slug} className="flex items-center gap-2 font-normal">
              <span className="truncate">{w.name ?? w.slug}</span>
              <span className="ml-auto text-xs text-ink-3">{WORKSPACE_ROLE_LABELS[w.role]}</span>
            </DropdownMenuLabel>
          ))
        ) : (
          <DropdownMenuItem onSelect={copyUid}>
            <span className="text-xs">所属しているワークスペースがありません。uid をコピー</span>
            <Copy className="ml-auto size-3.5" />
          </DropdownMenuItem>
        )}
        {workspace && (
          <>
            {/* スマホ幅では上部のバーから「知識」「規約」「可観測性」「設定」を外すので、ここから行けるようにする */}
            <DropdownMenuSeparator className="sm:hidden" />
            <DropdownMenuItem className="sm:hidden" onSelect={() => navigate(paths.knowledge(workspace.slug))}>
              知識
            </DropdownMenuItem>
            <DropdownMenuItem className="sm:hidden" onSelect={() => navigate(paths.policies(workspace.slug))}>
              規約
            </DropdownMenuItem>
            <DropdownMenuItem className="sm:hidden" onSelect={() => navigate(paths.observability(workspace.slug))}>
              可観測性
            </DropdownMenuItem>
            <DropdownMenuItem className="sm:hidden" onSelect={() => navigate(paths.settings(workspace.slug))}>
              設定
            </DropdownMenuItem>
          </>
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
  )
}
