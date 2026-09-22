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
