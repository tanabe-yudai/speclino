/** 検査の条件（run-manifest）の表示用整形。 */

import { TRIGGER_LABELS } from './labels'
import type { RunManifest } from '../types/run'

export function describeLimits(manifest: RunManifest): string {
  const { limits } = manifest
  return `呼出しの深さ ${limits.depth}・経路の上限 ${limits.max_paths}・判定の制限時間 ${limits.solver_timeout_ms}ms`
}

export function describeMode(manifest: RunManifest): string {
  return manifest.mode === 'blocking' ? '必須' : '警告のみ'
}

/** base との意味差分を 1 行に要約する。base が無い run は null。 */
export function describeSemanticDiff(manifest: RunManifest): string | null {
  const diff = manifest.semantic_diff
  if (!diff) return null
  if (!diff.base_available) {
    return `比較元との差分: 比較できません${diff.base_error ? `（${diff.base_error}）` : ''}`
  }
  const c = diff.counts
  return (
    `比較元との差分: 新規違反 ${c.new_violation} ・ 既存違反 ${c.existing_violation} ・ ` +
    `解消 ${c.resolved_violation} ・ 判定不能化 ${c.became_undecidable}`
  )
}

/** 適用したチーム規約の改訂（どの改訂で判定したか）。 */
export function describeTeamPolicy(manifest: RunManifest): string {
  if (!manifest.policy_team || manifest.policy_revision == null) return 'チーム規約: 適用なし'
  return `チーム規約: ${manifest.policy_team} 改訂 ${manifest.policy_revision}`
}

/** 検査が始まったきっかけ。未知の値は生の文字列を出さず「その他」にする。 */
export function triggerLabel(trigger: string): string {
  return TRIGGER_LABELS[trigger] ?? 'その他'
}
