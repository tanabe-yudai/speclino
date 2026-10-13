import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { PaneViewSwitch } from './PaneViewSwitch'

describe('PaneViewSwitch', () => {
  it('marks the current view as pressed and is shown at every width', () => {
    const html = renderToStaticMarkup(<PaneViewSwitch view="preview" onChange={() => {}} diagnosticCount={2} />)
    expect(html).not.toContain('lg:hidden')
    expect(html).toMatch(/aria-pressed="false"[^>]*>本文</)
    expect(html).toMatch(/aria-pressed="true"[^>]*>プレビュー（診断 2）</)
  })
})
