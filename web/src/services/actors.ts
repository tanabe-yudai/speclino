/** メンバーの uid を人が読む表示にする。内部の uid は画面に出さない。 */

import type { WorkspaceMember } from '@/types/member'

export const UNKNOWN_ACTOR_LABEL = 'メンバー'

/** uid → メールアドレス。メールを持たない・一覧に無い uid は「メンバー」。 */
export function actorLabel(uid: string | null | undefined, members: WorkspaceMember[] | null): string {
  if (!uid) return UNKNOWN_ACTOR_LABEL
  return members?.find((m) => m.uid === uid)?.email ?? UNKNOWN_ACTOR_LABEL
}
