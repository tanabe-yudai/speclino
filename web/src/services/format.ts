/** 表示用の整形ロジック（日付・SHA の短縮など）。 */

export function shortSha(sha: string | null | undefined, length = 7): string {
  if (!sha) return '-'
  return sha.slice(0, length)
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat('ja-JP', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

/** 検査の対象: PR なら「PR #7」、PR が無ければ「手動」。 */
export function runTargetLabel(prNumber: number | null | undefined): string {
  return prNumber != null ? `PR #${prNumber}` : '手動'
}

/** 検査の一覧の行の見出し: 「shop PR #7」「shop 手動」（owner は省く）。リポジトリが分からなければ「(リポジトリ不明)」。 */
export function runRowLabel(repoFullName: string | null | undefined, prNumber: number | null | undefined): string {
  const repo = repoFullName ? (repoFullName.split('/')[1] ?? repoFullName) : '(リポジトリ不明)'
  return `${repo} ${runTargetLabel(prNumber)}`
}
