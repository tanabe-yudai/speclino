/** ⌘K の候補。所属ワークスペースとそのプロジェクトから作る（ネットワークは呼ばない）。 */

import { AGENT_CONNECTION_LABEL, SECTION_LABELS } from './labels'
import { SECTIONS, sectionPath } from './navSections'
import { paths } from './paths'
import type { Command } from '@/types/command'
import type { Me } from '@/types/me'
import type { ProjectSummary } from '@/types/project'

export function buildCommands(input: {
  me: Me | null
  projects: Record<string, ProjectSummary[]>
}): Command[] {
  const commands: Command[] = [{ id: 'home', label: 'ホーム', group: '画面', path: paths.home() }]
  for (const ws of input.me?.workspaces ?? []) {
    const label = ws.name ?? ws.slug
    const add = (id: string, name: string, path: string) =>
      commands.push({ id: `${id}:${ws.slug}`, label: `${label} の${name}`, group: 'ワークスペース', path })
    for (const s of SECTIONS) add(s === 'home' ? 'workspace' : s, SECTION_LABELS[s], sectionPath(ws.slug, s))
    add('agents', AGENT_CONNECTION_LABEL, paths.settings(ws.slug, 'agents'))
    for (const repo of input.projects[ws.slug] ?? []) {
      commands.push({
        id: `project:${ws.slug}:${repo.repo_full_name}`,
        label: repo.repo_full_name,
        group: 'プロジェクト',
        path: paths.project(ws.slug, repo.repo_full_name),
      })
    }
  }
  return commands
}
