/** Server-Sent Events の 1 イベント（`event:` と、`data:` を `\n` でつないだ文字列）。 */
export interface SseMessage {
  event: string
  data: string
}
