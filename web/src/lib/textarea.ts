import { lineOffset } from '@/services/specPreview'

/** textarea の 1 始まりの行・列へ移る（選択して、その行が見えるようにスクロールする）。 */
export function focusLine(el: HTMLTextAreaElement, line: number, column: number) {
  const offset = lineOffset(el.value, line, column)
  el.focus()
  el.setSelectionRange(offset, offset)
  const lineHeight = Number.parseFloat(getComputedStyle(el).lineHeight) || 20
  el.scrollTop = Math.max(0, (line - 3) * lineHeight)
}
