import type { ReactNode } from 'react'

/** 第 1 階層の画面の見出し（英字の小見出し・題・説明）。 */
export function PageHeader({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <header>
      <p className="text-xs font-semibold tracking-widest text-primary uppercase">{eyebrow}</p>
      <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{title}</h1>
      {children && <p className="mt-1 text-sm text-ink-2">{children}</p>}
    </header>
  )
}
