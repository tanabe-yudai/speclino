import { ChevronDown } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useMe } from '@/hooks/useMe'
import { WORKSPACE_ROLE_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import { PILL_CLASS } from './pill'

/** ワークスペースの切り替え。所属が無ければ出さない。 */
export function WorkspacePill() {
  const { me } = useMe()
  const current = useCurrentWorkspace()
  const navigate = useNavigate()
  if (!current || !me) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={PILL_CLASS}>
        <span className="truncate font-semibold">{current.name ?? current.slug}</span>
        <ChevronDown className="size-3.5 shrink-0 text-ink-3" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>ワークスペース</DropdownMenuLabel>
        {me.workspaces.map((w) => (
          <DropdownMenuItem key={w.slug} onSelect={() => navigate(paths.workspace(w.slug))}>
            <span className="truncate">{w.name ?? w.slug}</span>
            <span className="ml-auto text-xs text-ink-3">{WORKSPACE_ROLE_LABELS[w.role]}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
