/**
 * ゲート結果の理由テキストを組み立てる。
 *
 * 注意: バックエンドの GateResult.reason（src/speclino/conformance/gate.py）は
 * review.md にしか含まれず、review-runs API は gate_status（enum）しか返さない。
 * ここでは manifest.fail_on と findings の件数から妥当な理由文を再構成する
 * （正確な reason 文字列そのものではない。詳細は review.md タブを参照）。
 */

import type { Finding, Verdict } from '../types/finding'
import type { GateStatus, RunManifest } from '../types/run'
import { attestationTarget, recordedAttestation } from './attestation'
import { VERDICT_LABELS } from './labels'
import { countByVerdict } from './verdict'

const DEFAULT_FAIL_ON: Verdict[] = ['VIOLATED', 'UNKNOWN', 'REVIEW']

/**
 * ゲートを止めない REVIEW（バックエンドの gate の cleared と同じ規則）: LLM 支援の「指摘なし」と、
 * この run で `attested` の確定が効いた指摘。失敗の理由の件数に数えない。
 */
function clearedReview(finding: Finding, manifest: RunManifest | null): boolean {
  if (finding.verdict !== 'REVIEW') return false
  if (finding.assessment_outcome === 'no_issue') return true
  const target = attestationTarget(finding)
  const recorded = target ? recordedAttestation(target, manifest?.attestations) : null
  return recorded?.disposition === 'attested'
}

export function describeGateReason(
  gateStatus: GateStatus | null,
  manifest: RunManifest | null,
  findings: Finding[],
): string {
  if (gateStatus === null) {
    return '実行がまだ完了していないため、ゲートは未確定です'
  }
  if (gateStatus === 'pass') {
    return 'すべての必須要求が確認範囲内で適合しています'
  }
  if (gateStatus === 'pass_with_exceptions') {
    return '登録済みの例外により通過しています（一部は要確認のまま残っています）'
  }

  const counts = countByVerdict(findings.filter((f) => !clearedReview(f, manifest)))
  const failOn = new Set<string>(manifest?.fail_on ?? DEFAULT_FAIL_ON)
  const blocking = DEFAULT_FAIL_ON.filter((verdict) => failOn.has(verdict) && counts[verdict] > 0)
  if (blocking.length === 0) {
    return '必須ルールの判定が失敗基準を満たしているため失敗しました'
  }
  const parts = blocking.map((verdict) => `${VERDICT_LABELS[verdict]} ${counts[verdict]}件`)
  return `${parts.join(' / ')} が残っているため失敗しました`
}
