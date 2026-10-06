import { describe, expect, it } from 'vitest'
import html from '../../index.html?raw'

describe('index.html', () => {
  it('タイトルと説明と OGP がある', () => {
    expect(html).toMatch(/<title>SpecLino<\/title>/)
    expect(html).toMatch(/<meta name="description" content="[^"]+"/)
    expect(html).toMatch(/<meta property="og:title" content="[^"]+"/)
    expect(html).toMatch(/<meta property="og:description" content="[^"]+"/)
    expect(html).toMatch(/<meta property="og:type" content="website"/)
    expect(html).toMatch(/<meta name="twitter:card" content="summary"/)
  })

  it('og:url を置かず、「デモ」や個人の環境の値を書かない', () => {
    expect(html).not.toMatch(/og:url|og:image/)
    expect(html).not.toMatch(/デモ|tanabe|example-project|run\.app/i)
  })
})
