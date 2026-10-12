/** 表示名の入力の確認（API の services/profiles.py と同じ規則）。判断は API が行い、ここは先に示すだけ。 */

export const DISPLAY_NAME_MAX_LENGTH = 50
// 制御文字・書式文字（文字の向きを変える U+202E など）・行と段落の区切り
const REJECTED = /[\p{Cc}\p{Cf}\u2028\u2029]/u

export type DisplayNameInput = { ok: true; value: string | null } | { ok: false; message: string }

/** 前後の空白を除く。空なら null（表示名を消す）。 */
export function parseDisplayName(raw: string): DisplayNameInput {
  const value = raw.trim()
  if (!value) return { ok: true, value: null }
  // API は Unicode のコードポイントで数える
  if (Array.from(value).length > DISPLAY_NAME_MAX_LENGTH) {
    return { ok: false, message: `表示名は ${DISPLAY_NAME_MAX_LENGTH} 文字までです` }
  }
  if (REJECTED.test(value)) return { ok: false, message: '表示名に改行や制御文字は使えません' }
  // メールアドレスの形を名乗らせない（作者・承認者の表示でなりすませないように）
  if (!/^[\p{L}\p{Nd} ._\-'・]+$/u.test(value)) {
    return { ok: false, message: "表示名に使えるのは文字・数字・空白と . - _ ' ・ だけです" }
  }
  return { ok: true, value }
}
