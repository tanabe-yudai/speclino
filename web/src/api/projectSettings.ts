/** project の設定とオンボーディングのエンドポイント関数（S47）。VITE_MOCK=1 のときは見本を返す。 */

import { apiFetch } from './client'
import type { ProjectSettings, ProjectSettingsView, WorkspaceOnboarding } from '../types/projectSettings'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

const base = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}`

const MOCK_SETTINGS: ProjectSettings = {
  fail_on: ['VIOLATED', 'UNKNOWN', 'REVIEW'],
  agent_enabled: true,
  test_run_enabled: true,
  approvers: [],
}
const mockSettings: Record<string, ProjectSettings> = {}

export async function getOnboarding(slug: string): Promise<WorkspaceOnboarding> {
  if (USE_MOCK) {
    return {
      can_edit_settings: true,
      projects: [
        {
          project_id: 'mock-project',
          repo_full_name: 'example-org/orders',
          steps: [
            { id: 'app', state: 'done', detail: 'GitHub App がインストールされ、リポジトリが登録済み' },
            { id: 'speclino', state: 'done', detail: '検査が完了し、設定ファイルを読めました' },
            { id: 'first_run', state: 'done', detail: '検査が完了しています' },
          ],
        },
      ],
    }
  }
  return apiFetch<WorkspaceOnboarding>(`${base(slug)}/onboarding`)
}

export async function getProjectSettings(slug: string, projectId: string): Promise<ProjectSettingsView> {
  if (USE_MOCK) {
    return {
      project_id: projectId,
      repo_full_name: 'example-org/orders',
      settings: mockSettings[projectId] ?? MOCK_SETTINGS,
      stored_invalid: false,
      can_edit: true,
      can_edit_approvers: true,
    }
  }
  return apiFetch<ProjectSettingsView>(`${base(slug)}/projects/${encodeURIComponent(projectId)}/settings`)
}

export async function updateProjectSettings(
  slug: string,
  projectId: string,
  settings: ProjectSettings,
): Promise<ProjectSettingsView> {
  if (USE_MOCK) {
    mockSettings[projectId] = settings
    return getProjectSettings(slug, projectId)
  }
  return apiFetch<ProjectSettingsView>(`${base(slug)}/projects/${encodeURIComponent(projectId)}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
}
