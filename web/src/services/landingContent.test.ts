import { describe, expect, it } from 'vitest'
import * as content from './landingContent'

function strings(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(strings)
  if (value && typeof value === 'object') return Object.values(value).flatMap(strings)
  return []
}

const all = strings(content)

describe('landingContent', () => {
  it('「デモ」と言わない', () => {
    expect(all.filter((s) => /デモ|demo/i.test(s))).toEqual([])
  })

  it('個人の環境の値を書かない', () => {
    const banned = /tanabe|example-project|6h5m3svkja|gmail\.com|run\.app/i
    expect(all.filter((s) => banned.test(s))).toEqual([])
  })

  it('はじめ方は 01〜04、原則は 3 つ', () => {
    expect(content.STEPS.map((s) => s.number)).toEqual(['01', '02', '03', '04'])
    expect(content.PRINCIPLES).toHaveLength(3)
  })

  it('承認コメントの見本は内容ハッシュを取る形', () => {
    expect(content.SAMPLE_SPEC.comment).toMatch(/^\/speclino approve-spec sha256:/)
  })

  it('見本の検査は 4 値だけを使い、違反には反例がある', () => {
    for (const c of content.SAMPLE_PR.checks) {
      expect(['VERIFIED_IN_SCOPE', 'VIOLATED', 'UNKNOWN', 'REVIEW']).toContain(c.verdict)
      if (c.verdict === 'VIOLATED') expect(c.counterexample).toBeTruthy()
    }
  })

  it('末尾は招待制の一文', () => {
    expect(content.INVITE_ONLY).toBe('SpecLino は現在、招待制で提供しています。')
  })
})
