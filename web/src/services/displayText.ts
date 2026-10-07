/** バックエンドの文面から、画面に出さない開発者向けの断片（スラッシュコマンド）を取り除く。 */

const BACKTICK_COMMAND = /`[^`]*\/speclino[^`]*`/g
const BARE_COMMAND = /\/speclino\s+\S+(?:\s+\S+){0,2}/g

/** バックエンドの文面の内部の言葉を、画面の言葉にそろえる（役割名・AI）。 */
export function plainTerms(text: string): string {
  return text
    .replace(/\s*owner\s*\/\s*approver\s*/g, 'オーナーか承認者')
    .replace(/\bLLM\b/g, 'AI')
}

/** 「`/speclino attest …`」のようなコマンドを消し、残った句読点を整える。読める文字が残らなければ null。 */
export function stripCommands(text: string): string | null {
  const cleaned = plainTerms(text)
    .replace(BACKTICK_COMMAND, '')
    .replace(BARE_COMMAND, '')
    .replace(/[（(]\s*(?:PR\s*コメント\s*)?[）)]/g, '')
    .replace(/\s+([。、，．])/g, '$1')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/[:：、,\s]+$/, '')
    .trim()
  return /[\p{L}\p{N}]/u.test(cleaned) ? cleaned : null
}

export function stripCommandsFromList(items: string[]): string[] {
  return items.flatMap((item) => {
    const cleaned = stripCommands(item)
    return cleaned === null ? [] : [cleaned]
  })
}
