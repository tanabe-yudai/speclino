import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** 画面の見本の外枠。中身は飾りなので aria-hidden にし、label を読み上げ用に置く。 */
export function Sample({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <figure className={cn('min-w-0', className)}>
      <figcaption className="sr-only">{label}</figcaption>
      <div aria-hidden="true" className="pointer-events-none min-w-0 select-none">
        {children}
      </div>
    </figure>
  )
}
