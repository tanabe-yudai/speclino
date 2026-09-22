import type { BadgeProps } from '../types/components'

export function Badge({ tone, children }: BadgeProps) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}
