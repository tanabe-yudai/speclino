import { describe, expect, it } from 'vitest'
import { loginReturnPath } from './loginReturn'

describe('loginReturnPath', () => {
  it('ログイン前の場所がなければホームへ戻す', () => {
    expect(loginReturnPath(undefined)).toBe('/app')
  })

  it('クエリ（設定のタブ）を落とさずに戻す', () => {
    expect(loginReturnPath({ pathname: '/w/acme/settings', search: '?tab=tokens' })).toBe('/w/acme/settings?tab=tokens')
  })

  it('フラグメント（招待リンクのトークン）を落とさずに戻す', () => {
    expect(loginReturnPath({ pathname: '/invite', search: '', hash: '#slinv_abc' })).toBe('/invite#slinv_abc')
  })

  it('クエリがなければパスだけ', () => {
    expect(loginReturnPath({ pathname: '/runs/1' })).toBe('/runs/1')
  })
})
