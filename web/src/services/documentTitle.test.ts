import { describe, expect, it } from 'vitest'
import { documentTitle, runPageTitle } from './documentTitle'

describe('documentTitle', () => {
  it('画面名を前に置き、SpecLino を後ろに付ける', () => {
    expect(documentTitle('設定')).toBe('設定 · SpecLino')
  })

  it('画面名が無い（読み込み中・ランディング）ときは SpecLino だけ', () => {
    expect(documentTitle(undefined)).toBe('SpecLino')
    expect(documentTitle('')).toBe('SpecLino')
  })
})

describe('runPageTitle', () => {
  it('リポジトリ名（owner は除く）と PR 番号', () => {
    expect(runPageTitle('acme/shop', 45)).toBe('shop #45')
  })

  it('PR 番号が無ければリポジトリ名だけ、リポジトリも無ければ「検査」', () => {
    expect(runPageTitle('acme/shop', null)).toBe('shop')
    expect(runPageTitle(null, 3)).toBe('検査 #3')
  })
})
