import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { createAttestation } from '../api/attestations'
import { ApiError } from '../api/errors'
import type { AttestButtonProps } from '../types/components'
import { configChangeWarning, describeAttestation } from '../services/attestation'
import type { Attestation, Disposition } from '../types/attestation'

/**
 * REVIEW の確定（S70）。owner / approver にだけ出す。verdict は変わらず、確定は次の検査の
 * ゲートに効く（サーバーが同じ PR の再検査を作る）。
 */
export function AttestButton({ slug, runId, target, onDone }: AttestButtonProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<Attestation | null>(null)

  function submit(disposition: Disposition) {
    setPending(true)
    setError(null)
    createAttestation(slug, {
      fingerprint: target.fingerprint,
      content_hash: target.contentHash,
      disposition,
      run_id: runId,
    })
      .then((res) => {
        setDone(res.attestation)
        onDone(res.recheck_run_id)
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : '確定に失敗しました')
      })
      .finally(() => setPending(false))
  }

  if (done) {
    return (
      <p className="text-sm text-ink-2">
        {describeAttestation(done)}（再検査で反映されます）
        {configChangeWarning(done) && (
          <span role="alert" className="block font-semibold text-bad">
            {configChangeWarning(done)}
          </span>
        )}
      </p>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" onClick={() => submit('attested')} disabled={pending}>
        問題なしとして確定
      </Button>
      <Button size="sm" variant="secondary" onClick={() => submit('acknowledged_violation')} disabled={pending}>
        問題として記録（阻止したまま）
      </Button>
      {error && <span className="text-sm text-bad">{error}</span>}
    </div>
  )
}
