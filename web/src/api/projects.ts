/** プロジェクト（S45）のエンドポイント関数。一覧は slug ごとにキャッシュする。VITE_MOCK=1 のときは固定値。 */

import { apiFetch } from './client'
import { MOCK_OVERVIEW, MOCK_PROJECTS } from './mock/projects'
import { createRequestCache } from '../services/requestCache'
import type { ProjectOverview, ProjectSummary } from '../types/project'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

const projectsCache = createRequestCache<ProjectSummary[]>((slug) =>
  USE_MOCK
    ? Promise.resolve(MOCK_PROJECTS)
    : apiFetch<ProjectSummary[]>(`/api/workspaces/${encodeURIComponent(slug)}/projects`),
)

export function listProjects(slug: string): Promise<ProjectSummary[]> {
  return projectsCache.get(slug)
}

export function invalidateProjects(slug?: string): void {
  projectsCache.invalidate(slug)
}

export async function getProjectOverview(slug: string, projectId: string): Promise<ProjectOverview> {
  if (USE_MOCK) return { ...MOCK_OVERVIEW, project: MOCK_PROJECTS.find((p) => p.id === projectId) ?? MOCK_OVERVIEW.project }
  return apiFetch<ProjectOverview>(
    `/api/workspaces/${encodeURIComponent(slug)}/projects/${encodeURIComponent(projectId)}/overview`,
  )
}
