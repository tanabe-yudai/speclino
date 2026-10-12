/** ワークスペースのホームの「やること」のうち、観測の集計から作るもの。 */

import type { WorkspaceHealth } from '@/types/observability'

export interface StaleRepository {
  repo: string
  behindBy: number
}

/**
 * リポジトリ同期（speclino sync）で最後に取った知識が今の bundle より古いリポジトリ
 * （ゲートでは KNOWLEDGE-LOCK-001 が知らせる）。失効したトークンの記録は数えない。
 */
export function staleRepositories(health: WorkspaceHealth): StaleRepository[] {
  const worst = new Map<string, number>()
  for (const f of health.fetches) {
    if (f.channel !== 'sync' || f.revoked || !f.repo_full_name || !f.behind_by) continue
    worst.set(f.repo_full_name, Math.max(worst.get(f.repo_full_name) ?? 0, f.behind_by))
  }
  return [...worst.entries()].map(([repo, behindBy]) => ({ repo, behindBy })).sort((a, b) => a.repo.localeCompare(b.repo))
}
