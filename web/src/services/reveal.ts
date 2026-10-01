/** 見本の判定を開いてから stepMs ごとに 1 件ずつ確定させる。動きを減らす設定では最初から全部。 */
export function revealedCount(elapsedMs: number, stepMs: number, total: number, reducedMotion: boolean): number {
  if (reducedMotion) return total
  if (elapsedMs <= 0) return 0
  return Math.min(total, Math.floor(elapsedMs / stepMs))
}
