import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SupplementList } from './SupplementList'

const SUPPLEMENT = {
  id: '1',
  requirement_id: 'R-1',
  question: 'q',
  content: 'リトライで吸収',
  created_at: '2026-10-04T00:00:00Z',
  source: 'claude-code',
}

describe('SupplementList', () => {
  it('shows supplements as non-official notes under the REVIEW finding', () => {
    const html = renderToStaticMarkup(<SupplementList supplements={[SUPPLEMENT]} />)
    expect(html).toContain('エージェントからの補足（正式な回答ではありません）')
    expect(html).toContain('リトライで吸収')
    expect(html).toContain('claude-code')
  })

  it('escapes content instead of rendering it as HTML', () => {
    const html = renderToStaticMarkup(<SupplementList supplements={[{ ...SUPPLEMENT, content: '<b>x</b>' }]} />)
    expect(html).not.toContain('<b>x</b>')
  })

  it('renders nothing when there are no supplements', () => {
    expect(renderToStaticMarkup(<SupplementList supplements={[]} />)).toBe('')
  })
})
