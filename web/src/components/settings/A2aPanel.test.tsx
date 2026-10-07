import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { A2aPanel } from './A2aPanel'

describe('A2aPanel', () => {
  it('Agent Card の URL と接続の案内を表示し、長い URL は折り返す', () => {
    const html = renderToStaticMarkup(<A2aPanel origin="https://speclino.example.com" />)
    expect(html).toContain('エージェントから相談する（A2A）')
    expect(html).toContain('https://speclino.example.com/.well-known/agent-card.json')
    expect(html).toContain('エージェント接続トークン')
    expect(html).toContain('break-all')
  })
})
