import { useEffect, useState } from 'react'
import { revealedCount } from '@/services/reveal'
import { usePrefersReducedMotion } from './usePrefersReducedMotion'

/** マウントしてからの経過で、確定した件数を返す（services/reveal.ts の規則）。 */
export function useReveal(total: number, stepMs: number): number {
  const reduced = usePrefersReducedMotion()
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (reduced) return
    const start = performance.now()
    const id = window.setInterval(() => {
      const ms = performance.now() - start
      setElapsed(ms)
      if (ms >= total * stepMs) window.clearInterval(id)
    }, 100)
    return () => window.clearInterval(id)
  }, [reduced, total, stepMs])
  return revealedCount(elapsed, stepMs, total, reduced)
}
