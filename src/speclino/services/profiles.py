"""利用者のプロフィール（表示名とメール）と、uid の表示用の名前。

- サインインした利用者の ID トークンのメール・確認状態を `GET /api/me` のたびに記録する
  （変わったときだけ書く）。表示名は行を作るときだけトークンの name を入れ、以後は本人の設定を
  上書きしない
- 表示名を変えられるのは本人だけ（`update_display_name` は呼び出した人の uid にだけ書く）
- 画面に出す名前は、表示名があれば必ず「表示名（メール、無ければ uid の短縮）」にする。表示名は
  本人が自由に決められるので、同名の別人や他人の名前を名乗る人を見分けられるようにする。未確認の
  メールには「・未確認」を添える。表示名が無ければメール、それも無ければ uid の短縮。表示名に
  `@` は使えない（メールの形を名乗らせない）。プロフィール（メールを含む）を解決するのは
  同じワークスペースのメンバーの uid だけで、それ以外の uid は短縮表示にする（別のワークスペースの
  人の名前・メールを漏らさない）。GitHub の actor（`github:<id>`）はそのまま出す
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import UserProfileRecord, WorkspaceMember
from speclino.domain.workspace import AuthUser, UserProfile, WorkspaceMemberView

DISPLAY_NAME_MAX_LENGTH = 50
GITHUB_ACTOR_PREFIX = "github:"
_SHORT_UID_LENGTH = 8
# 制御文字・書式文字（文字の向きを変える U+202E など）・行と段落の区切り
_REJECTED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


class InvalidDisplayName(ValueError):
    """表示名が規則（前後の空白を除いて 1〜50 文字、制御文字と `@` なし）に合わない。"""


# 表示名に使える記号。文字（L*）・数字（N*）・空白（Zs）以外はこれだけを許す（許す側を列挙する。
# 禁止する側を列挙すると、@ や括弧に似た文字で表示の添え書き「表示名（メール）」を偽装できる）
_ALLOWED_PUNCT = frozenset(" .-_'\u30fb")


def normalize_display_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise InvalidDisplayName("表示名が空")
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        raise InvalidDisplayName(f"表示名は {DISPLAY_NAME_MAX_LENGTH} 文字まで")
    if any(unicodedata.category(ch) in _REJECTED_CATEGORIES for ch in name):
        raise InvalidDisplayName("表示名に制御文字は使えない")
    for ch in name:
        if (
            ch in _ALLOWED_PUNCT
            or unicodedata.category(ch)[0] == "L"
            or unicodedata.category(ch) == "Nd"
        ):
            continue
        raise InvalidDisplayName(
            "表示名に使えるのは文字・数字・空白と . - _ ' ・ だけ（メールや添え書きを名乗れない）"
        )
    return name


def short_uid(uid: str) -> str:
    return uid if len(uid) <= _SHORT_UID_LENGTH + 1 else f"{uid[:_SHORT_UID_LENGTH]}…"


def profile_label(
    uid: str, display_name: str | None, email: str | None, email_verified: bool
) -> str:
    """表示名（メール、無ければ uid の短縮）→ メール → uid の短縮。未確認のメールには印を添える。"""
    if display_name:
        if not email:
            return f"{display_name}（{short_uid(uid)}）"
        return f"{display_name}（{email}{'' if email_verified else '・未確認'}）"
    if email:
        return email if email_verified else f"{email}（未確認）"
    return short_uid(uid)


def _token_name(user: AuthUser) -> str | None:
    """ID トークンの name を表示名の初期値に使えるなら返す（使えなければ入れない）。"""
    if user.name is None:
        return None
    try:
        return normalize_display_name(user.name)
    except InvalidDisplayName:
        return None


def _view(row: UserProfileRecord) -> UserProfile:
    return UserProfile(
        uid=row.uid,
        display_name=row.display_name,
        email=row.email,
        email_verified=row.email_verified,
    )


async def record_sign_in(session: AsyncSession, user: AuthUser) -> UserProfile:
    """ID トークンのメールと確認状態を記録し、プロフィールを返す。

    行が無ければ作り、表示名にトークンの name を入れる。行があれば表示名には触らず、メールか
    確認状態が変わったときだけ書く。
    """
    table = UserProfileRecord.__table__
    stmt = insert(UserProfileRecord).values(
        uid=user.uid,
        display_name=_token_name(user),
        email=user.email,
        email_verified=user.email_verified,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserProfileRecord.uid],
        set_={
            "email": stmt.excluded.email,
            "email_verified": stmt.excluded.email_verified,
            "updated_at": func.now(),
        },
        where=or_(
            table.c.email.is_distinct_from(stmt.excluded.email),
            table.c.email_verified.is_distinct_from(stmt.excluded.email_verified),
        ),
    )
    await session.execute(stmt)
    row = await session.get(UserProfileRecord, user.uid, populate_existing=True)
    assert row is not None
    return _view(row)


async def update_display_name(
    session: AsyncSession, user: AuthUser, display_name: str | None
) -> UserProfile:
    """本人の表示名を変える。None で消す（画面ではメールか uid の短縮に戻る）。"""
    name = None if display_name is None else normalize_display_name(display_name)
    stmt = insert(UserProfileRecord).values(
        uid=user.uid,
        display_name=name,
        email=user.email,
        email_verified=user.email_verified,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserProfileRecord.uid],
        set_={"display_name": name, "updated_at": func.now()},
    )
    await session.execute(stmt)
    row = await session.get(UserProfileRecord, user.uid, populate_existing=True)
    assert row is not None
    return _view(row)


async def member_views(
    session: AsyncSession, workspace: str, uids: Collection[str] | None = None
) -> list[WorkspaceMemberView]:
    """ワークスペースのメンバー（uids を渡せばその中のメンバーだけ）とプロフィール。

    メールはプロフィールのものを優先し、無ければ招待の受諾で入ったメール（確認済み）。
    """
    stmt = (
        select(WorkspaceMember, UserProfileRecord)
        .outerjoin(UserProfileRecord, UserProfileRecord.uid == WorkspaceMember.uid)
        .where(WorkspaceMember.team == workspace)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.uid)
    )
    if uids is not None:
        stmt = stmt.where(WorkspaceMember.uid.in_(list(uids)))
    out: list[WorkspaceMemberView] = []
    for member, profile in (await session.execute(stmt)).tuples():
        display_name = profile.display_name if profile is not None else None
        if profile is not None and profile.email:
            email: str | None = profile.email
            verified = profile.email_verified
        else:
            email, verified = member.email, member.email is not None
        out.append(
            WorkspaceMemberView(
                uid=member.uid,
                display_name=display_name,
                email=email,
                email_verified=verified,
                label=profile_label(member.uid, display_name, email, verified),
                role=member.role,
                joined_at=member.created_at,
            )
        )
    return out


async def display_names(
    session: AsyncSession, *, workspace: str, uids: Collection[str]
) -> dict[str, str]:
    """uid → 画面に出す名前。workspace のメンバーだけをプロフィールで解決する。"""
    wanted = {u for u in uids if u}
    lookup = [u for u in wanted if not u.startswith(GITHUB_ACTOR_PREFIX)]
    resolved = (
        {v.uid: v.label for v in await member_views(session, workspace, lookup)} if lookup else {}
    )
    return {
        u: u if u.startswith(GITHUB_ACTOR_PREFIX) else resolved.get(u, short_uid(u)) for u in wanted
    }


__all__ = [
    "DISPLAY_NAME_MAX_LENGTH",
    "InvalidDisplayName",
    "display_names",
    "member_views",
    "normalize_display_name",
    "profile_label",
    "record_sign_in",
    "short_uid",
    "update_display_name",
]
