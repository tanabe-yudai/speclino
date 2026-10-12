import { ChevronDown } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { paths } from '@/services/paths'
import { cn } from '@/lib/utils'
import { PILL_CLASS } from './pill'

/** 今のワークスペースに登録されたリポジトリ（プロジェクト）の切り替え。 */
export function ProjectPill() {
  const workspace = useCurrentWorkspace()
  const { owner, repo } = useParams<{ owner?: string; repo?: string }>()
  const navigate = useNavigate()
  const slug = workspace?.slug
  const repos = useWorkspaceProjects(slug).projects ?? []

  if (!slug) return null
  const current = owner && repo ? `${owner}/${repo}` : null
  return (
    <DropdownMenu>
      {/* スマホ幅の上部バーはワークスペース名とアカウントだけ（プロジェクトはホームのカードから選ぶ） */}
      <DropdownMenuTrigger className={cn(PILL_CLASS, 'hidden lg:inline-flex')}>
        <span className="truncate">{current ? current.split('/')[1] : 'プロジェクト'}</span>
        <ChevronDown className="size-3.5 shrink-0 text-ink-3" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>プロジェクト</DropdownMenuLabel>
        {repos.map((r) => (
          <DropdownMenuItem key={r.repo_full_name} onSelect={() => navigate(paths.project(slug, r.repo_full_name))}>
            <span className="truncate font-mono text-xs">{r.repo_full_name}</span>
          </DropdownMenuItem>
        ))}
        {repos.length > 0 && <DropdownMenuSeparator />}
        <DropdownMenuItem onSelect={() => navigate(paths.settings(slug, 'repositories'))}>
          リポジトリを追加・確認
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
