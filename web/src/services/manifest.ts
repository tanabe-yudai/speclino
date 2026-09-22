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
