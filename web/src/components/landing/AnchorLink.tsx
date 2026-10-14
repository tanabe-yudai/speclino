import type { ComponentProps, MouseEvent } from 'react'
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion'

/** ページ内の見出しへのリンク。なめらかにスクロールする（視差効果を減らす設定なら一気に移る）。 */
export function AnchorLink({ href, onClick, ...props }: ComponentProps<'a'> & { href: `#${string}` }) {
  const reduced = usePrefersReducedMotion()
  function handleClick(e: MouseEvent<HTMLAnchorElement>) {
    onClick?.(e)
    const target = document.getElementById(href.slice(1))
    if (!target || e.defaultPrevented) return
    e.preventDefault()
    target.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' })
    window.history.pushState(null, '', href)
  }
  return <a href={href} onClick={handleClick} {...props} />
}
