import { describe, expect, it } from 'vitest'
import { cn } from '@/lib/utils'

describe('cn', () => {
  it('Tailwind のクラスの衝突を後勝ちでまとめる', () => {
    expect(cn('p-2', 'p-4')).toBe('p-4')
  })
})
