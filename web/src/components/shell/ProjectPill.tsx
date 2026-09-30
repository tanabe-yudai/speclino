import { ChevronDown } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { listRepositories } from '@/api/repositories'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { paths } from '@/services/paths'
import type { RegisteredRepository } from '@/types/repository'
import { PILL_CLASS } from './pill'

/** 今のワークスペースに登録されたリポジトリ（プロジェクト）の切り替え。 */
export function ProjectPill() {
  const workspace = useCurrentWorkspace()
  const { owner, repo } = useParams<{ owner?: string; repo?: string }>()
  const navigate = useNavigate()
  const [repos, setRepos] = useState<RegisteredRepository[]>([])
  const slug = workspace?.slug

  useEffect(() => {
    if (!slug) return
    let alive = true
    listRepositories(slug)
      .then((r) => alive && setRepos(r))
      .catch(() => alive && setRepos([]))
    return () => {
      alive = false
    }
  }, [slug])

  if (!slug) return null
  const current = owner && repo ? `${owner}/${repo}` : null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={PILL_CLASS}>
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
