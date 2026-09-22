/** run-manifest（保証範囲・版）の表示用整形。 */

import type { RunManifest } from '../types/run'

export function describeLimits(manifest: RunManifest): string {
  const { limits, versions } = manifest
  return (
    `呼出し深さ ${limits.depth}・経路上限 ${limits.max_paths}・ソルバー timeout ${limits.solver_timeout_ms}ms` +
    `（解析器 ${versions.analyzer} / エンジン ${versions.engine} / ソルバー ${versions.solver}）`
  )
}

export function describeMode(manifest: RunManifest): string {
  return manifest.mode === 'blocking' ? '必須（blocking）' : '警告のみ（advisory）'
}

/** base との意味差分を 1 行に要約する。base が無い run は null。 */
export function describeSemanticDiff(manifest: RunManifest): string | null {
  const diff = manifest.semantic_diff
  if (!diff) return null
  if (!diff.base_available) {
    return `base との差分: 比較不能${diff.base_error ? `（${diff.base_error}）` : ''}`
  }
  const c = diff.counts
  return (
    `base との差分: 新規違反 ${c.new_violation} ・ 既存違反 ${c.existing_violation} ・ ` +
    `解消 ${c.resolved_violation} ・ 判定不能化 ${c.became_undecidable}`
  )
}
