import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listProjects } from '@/api/projects'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { useMe } from '@/hooks/useMe'
import { buildCommands } from '@/services/commands'
import type { CommandGroup as Group } from '@/types/command'
import type { ProjectSummary } from '@/types/project'

const GROUP_ORDER: Group[] = ['プロジェクト', 'ワークスペース', '画面']

/** ⌘K / Ctrl+K のパレット。開いたときに所属ワークスペースのプロジェクトを取る（一覧はキャッシュを共有）。 */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { me } = useMe()
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Record<string, ProjectSummary[]>>({})

  useEffect(() => {
    if (!open || !me) return
    let alive = true
    Promise.all(
      me.workspaces.map((w) =>
        listProjects(w.slug)
          .then((r) => [w.slug, r] as const)
          .catch(() => [w.slug, []] as const),
      ),
    ).then((entries) => alive && setProjects(Object.fromEntries(entries)))
    return () => {
      alive = false
    }
  }, [open, me])

  const commands = useMemo(() => buildCommands({ me, projects }), [me, projects])

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="移動" description="画面やプロジェクトへ移動する">
      <CommandInput placeholder="プロジェクト・画面を検索…" />
      <CommandList>
        <CommandEmpty>見つかりません</CommandEmpty>
        {GROUP_ORDER.map((group) => {
          const items = commands.filter((c) => c.group === group)
          if (items.length === 0) return null
          return (
            <CommandGroup key={group} heading={group}>
              {items.map((c) => (
                <CommandItem
                  key={c.id}
                  value={`${c.label} ${c.path}`}
                  onSelect={() => {
                    onOpenChange(false)
                    navigate(c.path)
                  }}
                >
                  {c.label}
                </CommandItem>
              ))}
            </CommandGroup>
          )
        })}
      </CommandList>
    </CommandDialog>
  )
}
