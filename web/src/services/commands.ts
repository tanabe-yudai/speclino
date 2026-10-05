/** ⌘K の候補。所属ワークスペースとそのプロジェクトから作る（ネットワークは呼ばない）。 */

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
    commands.push({
      id: `workspace:${ws.slug}`,
      label: `${label} のホーム`,
      group: 'ワークスペース',
      path: paths.workspace(ws.slug),
    })
    for (const repo of input.projects[ws.slug] ?? []) {
      commands.push({
        id: `project:${ws.slug}:${repo.repo_full_name}`,
        label: repo.repo_full_name,
        group: 'プロジェクト',
        path: paths.project(ws.slug, repo.repo_full_name),
      })
    }
    commands.push({
      id: `knowledge:${ws.slug}`,
      label: `${label} の知識（仕様・スキル・指示書）`,
      group: 'ワークスペース',
      path: paths.knowledge(ws.slug),
    })
    commands.push({
      id: `policies:${ws.slug}`,
      label: `${label} の規約`,
      group: 'ワークスペース',
      path: paths.policies(ws.slug),
    })
    commands.push({
      id: `settings:${ws.slug}`,
      label: `${label} の設定`,
      group: 'ワークスペース',
      path: paths.settings(ws.slug),
    })
  }
  return commands
}
