
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# Midnight NOIR Bot - 1ファイル版
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "ここにBOTトークン")
GUILD_ID = 1482224471606820874

# VCカテゴリ
QUICK_PUBLIC_CATEGORY_ID = 1521453832788246690
QUICK_PRIVATE_CATEGORY_ID = 1516009559892951060
PRIVATE_PUBLIC_CATEGORY_ID = 1521453832788246690
PRIVATE_HIDDEN_CATEGORY_ID = 1482309197814038538

# 性別・管理ロール
MALE_ROLE_ID = 1482301549353897984
FEMALE_ROLE_ID = 1523690396515962981
BOT_ADMIN_ROLE_ID = 1482306904918327336

# なう募集
NOW_RECRUIT_CHANNEL_ID = 1521066957103693965
MALE_PROFILE_CHANNEL_ID = 1482301104258547863
FEMALE_PROFILE_CHANNEL_ID = 1482301192263569522
MALE_NOTIFY_ROLE_ID = 1482301549353897984
FEMALE_NOTIFY_ROLE_ID = 1523690396515962981

# プロフィール審査
PROFILE_REVIEW_ROLE_ID = 1534024183233777706
TEMP_PROFILE_CHANNEL_ID = 1534024845799460964
PROFILE_REVIEW_CHANNEL_ID = 1534029103928184952
VERIFIED_ROLE_ID = 1482298544877736058

# 裏募集
RECRUIT_CREATE_PANEL_CHANNEL_ID = 1524090558518132907
RECRUIT_CONFIRM_CHANNEL_ID = 1529514190497386606
RECRUIT_NOTIFICATION_CHANNEL_ID = 1521066957103693965
RECRUIT_LOG_CHANNEL_ID = 1529623100986097764

# ノックあり部屋の外部ノックパネル投稿先
# 現在は「通知」チャンネルを使用します。
KNOCK_PANEL_CHANNEL_ID = 1521066957103693965
ANONYMOUS_NOTIFY_ROLE_ID = 1529620347157217331
NAMED_NOTIFY_ROLE_ID = 1529620408951771306

TIMED_ROOM_SECONDS = 10 * 60
EMPTY_DELETE_SECONDS = 15
DATABASE_PATH = "room_bot.sqlite3"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("noir-bot")


# =========================================================
# DB
# =========================================================

def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db_connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                owner_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                PRIMARY KEY (owner_id, target_id)
            );

            CREATE TABLE IF NOT EXISTS blacklist (
                owner_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                PRIMARY KEY (owner_id, target_id)
            );

            CREATE TABLE IF NOT EXISTS rooms (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                room_type TEXT NOT NULL,
                hidden_when_two INTEGER NOT NULL DEFAULT 0,
                timed INTEGER NOT NULL DEFAULT 0,
                timer_started INTEGER NOT NULL DEFAULT 0,
                knock_message_id INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS now_recruitments (
                message_id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                comment TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recruit_profiles (
                user_id INTEGER PRIMARY KEY,
                age TEXT NOT NULL DEFAULT '',
                voice TEXT NOT NULL DEFAULT '',
                personality TEXT NOT NULL DEFAULT '',
                style TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS recruitments (
                message_id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                conditions TEXT NOT NULL,
                mylist_only INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_message_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                applicant_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knock_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                applicant_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                dm_message_id INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS profile_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL UNIQUE,
                member_id INTEGER NOT NULL,
                profile_text TEXT NOT NULL,
                gender TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                review_message_id INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dm_settings (
                guild_id INTEGER PRIMARY KEY,
                panel_channel_id INTEGER NOT NULL,
                dm_ng_role_id INTEGER NOT NULL DEFAULT 0,
                dm_free_role_id INTEGER NOT NULL DEFAULT 0,
                log_channel_id INTEGER NOT NULL DEFAULT 0,
                panel_message_id INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS dm_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL DEFAULT 0,
                decision_message_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
            """
        )

        # 既存DB向けマイグレーション
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(rooms)").fetchall()
        }
        if "knock_message_id" not in columns:
            con.execute(
                "ALTER TABLE rooms ADD COLUMN knock_message_id INTEGER NOT NULL DEFAULT 0"
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_pair(table: str, owner_id: int, target_id: int) -> bool:
    try:
        with db_connect() as con:
            con.execute(
                f"INSERT INTO {table}(owner_id, target_id) VALUES (?, ?)",
                (owner_id, target_id),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_pair(table: str, owner_id: int, target_id: int) -> bool:
    with db_connect() as con:
        cur = con.execute(
            f"DELETE FROM {table} WHERE owner_id=? AND target_id=?",
            (owner_id, target_id),
        )
        return cur.rowcount > 0


def get_pairs(table: str, owner_id: int) -> list[int]:
    with db_connect() as con:
        rows = con.execute(
            f"SELECT target_id FROM {table} WHERE owner_id=? ORDER BY target_id",
            (owner_id,),
        ).fetchall()
    return [int(row["target_id"]) for row in rows]


def is_blocked_either_way(user_a: int, user_b: int) -> bool:
    with db_connect() as con:
        row = con.execute(
            """
            SELECT 1 FROM blacklist
            WHERE (owner_id=? AND target_id=?)
               OR (owner_id=? AND target_id=?)
            LIMIT 1
            """,
            (user_a, user_b, user_b, user_a),
        ).fetchone()
    return row is not None


def save_dm_settings(
    guild_id: int,
    panel_channel_id: int,
    dm_ng_role_id: int = 0,
    dm_free_role_id: int = 0,
    log_channel_id: int = 0,
    panel_message_id: int = 0,
) -> None:
    with db_connect() as con:
        con.execute(
            """
            INSERT INTO dm_settings(
                guild_id, panel_channel_id, dm_ng_role_id,
                dm_free_role_id, log_channel_id, panel_message_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                panel_channel_id=excluded.panel_channel_id,
                dm_ng_role_id=excluded.dm_ng_role_id,
                dm_free_role_id=excluded.dm_free_role_id,
                log_channel_id=excluded.log_channel_id,
                panel_message_id=excluded.panel_message_id
            """,
            (
                guild_id,
                panel_channel_id,
                dm_ng_role_id,
                dm_free_role_id,
                log_channel_id,
                panel_message_id,
            ),
        )


def get_dm_settings(guild_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM dm_settings WHERE guild_id=?",
            (guild_id,),
        ).fetchone()


def create_dm_request_record(
    guild_id: int,
    requester_id: int,
    target_id: int,
) -> int:
    with db_connect() as con:
        cur = con.execute(
            """
            INSERT INTO dm_requests(
                guild_id, requester_id, target_id, status, created_at
            ) VALUES (?, ?, ?, 'pending', ?)
            """,
            (guild_id, requester_id, target_id, utc_now()),
        )
        return int(cur.lastrowid)


def update_dm_request_location(
    request_id: int,
    thread_id: int,
    decision_message_id: int,
) -> None:
    with db_connect() as con:
        con.execute(
            """
            UPDATE dm_requests
            SET thread_id=?, decision_message_id=?
            WHERE id=?
            """,
            (thread_id, decision_message_id, request_id),
        )


def get_dm_request(request_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM dm_requests WHERE id=?",
            (request_id,),
        ).fetchone()


def set_dm_request_status(request_id: int, status: str) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE dm_requests SET status=? WHERE id=?",
            (status, request_id),
        )


def pending_dm_request_between(
    guild_id: int,
    requester_id: int,
    target_id: int,
) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            """
            SELECT * FROM dm_requests
            WHERE guild_id=? AND requester_id=? AND target_id=? AND status='pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (guild_id, requester_id, target_id),
        ).fetchone()


def all_pending_dm_requests() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM dm_requests WHERE status='pending'"
        ).fetchall()


def save_room(
    channel_id: int,
    guild_id: int,
    owner_id: int,
    room_type: str,
    hidden_when_two: bool,
    timed: bool,
) -> None:
    with db_connect() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO rooms
            (channel_id, guild_id, owner_id, room_type, hidden_when_two, timed, timer_started)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (channel_id, guild_id, owner_id, room_type, int(hidden_when_two), int(timed)),
        )


def get_room(channel_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM rooms WHERE channel_id=?",
            (channel_id,),
        ).fetchone()


def all_rooms() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute("SELECT * FROM rooms").fetchall()


def delete_room_record(channel_id: int) -> None:
    with db_connect() as con:
        con.execute("DELETE FROM rooms WHERE channel_id=?", (channel_id,))


def set_timer_started(channel_id: int, value: bool) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE rooms SET timer_started=? WHERE channel_id=?",
            (int(value), channel_id),
        )


def set_knock_message_id(channel_id: int, message_id: int) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE rooms SET knock_message_id=? WHERE channel_id=?",
            (message_id, channel_id),
        )


def save_now_recruitment(message_id: int, owner_id: int, target_type: str, comment: str) -> None:
    with db_connect() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO now_recruitments
            (message_id, owner_id, target_type, comment, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (message_id, owner_id, target_type, comment, utc_now()),
        )


def get_now_recruitment(message_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM now_recruitments WHERE message_id=?",
            (message_id,),
        ).fetchone()


def close_now_recruitment(message_id: int) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE now_recruitments SET active=0 WHERE message_id=?",
            (message_id,),
        )


def save_recruitment(
    message_id: int,
    owner_id: int,
    mode: str,
    title: str,
    body: str,
    conditions: str,
    mylist_only: bool,
) -> None:
    with db_connect() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO recruitments
            (message_id, owner_id, mode, title, body, conditions, mylist_only, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (message_id, owner_id, mode, title, body, conditions, int(mylist_only), utc_now()),
        )


def get_recruitment(message_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM recruitments WHERE message_id=?",
            (message_id,),
        ).fetchone()


def close_recruitment(message_id: int) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE recruitments SET active=0 WHERE message_id=?",
            (message_id,),
        )


def create_application(source_message_id: int, owner_id: int, applicant_id: int) -> int:
    with db_connect() as con:
        old = con.execute(
            """
            SELECT id FROM applications
            WHERE source_message_id=? AND applicant_id=? AND status='pending'
            """,
            (source_message_id, applicant_id),
        ).fetchone()
        if old:
            return int(old["id"])

        cur = con.execute(
            """
            INSERT INTO applications
            (source_message_id, owner_id, applicant_id, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (source_message_id, owner_id, applicant_id, utc_now()),
        )
        return int(cur.lastrowid)


def get_application(application_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM applications WHERE id=?",
            (application_id,),
        ).fetchone()


def update_application(application_id: int, status: str) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE applications SET status=? WHERE id=?",
            (status, application_id),
        )


def all_pending_applications() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM applications WHERE status='pending'"
        ).fetchall()


def create_knock_request(
    guild_id: int,
    channel_id: int,
    owner_id: int,
    applicant_id: int,
) -> int:
    with db_connect() as con:
        old = con.execute(
            """
            SELECT id FROM knock_requests
            WHERE channel_id=? AND applicant_id=? AND status='pending'
            ORDER BY id DESC LIMIT 1
            """,
            (channel_id, applicant_id),
        ).fetchone()
        if old:
            return int(old["id"])

        cur = con.execute(
            """
            INSERT INTO knock_requests(
                guild_id, channel_id, owner_id, applicant_id,
                status, dm_message_id, created_at
            ) VALUES (?, ?, ?, ?, 'pending', 0, ?)
            """,
            (guild_id, channel_id, owner_id, applicant_id, utc_now()),
        )
        return int(cur.lastrowid)


def get_knock_request(request_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM knock_requests WHERE id=?",
            (request_id,),
        ).fetchone()


def update_knock_request(request_id: int, status: str) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE knock_requests SET status=? WHERE id=?",
            (status, request_id),
        )


def set_knock_dm_message_id(request_id: int, message_id: int) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE knock_requests SET dm_message_id=? WHERE id=?",
            (message_id, request_id),
        )


def all_pending_knock_requests() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM knock_requests WHERE status='pending'"
        ).fetchall()



def create_profile_review(
    guild_id: int,
    source_message_id: int,
    member_id: int,
    profile_text: str,
    gender: str,
) -> int:
    with db_connect() as con:
        old = con.execute(
            "SELECT id FROM profile_reviews WHERE source_message_id=?",
            (source_message_id,),
        ).fetchone()
        if old:
            return int(old["id"])

        cur = con.execute(
            """
            INSERT INTO profile_reviews(
                guild_id, source_message_id, member_id,
                profile_text, gender, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                guild_id,
                source_message_id,
                member_id,
                profile_text,
                gender,
                utc_now(),
            ),
        )
        return int(cur.lastrowid)


def get_profile_review(review_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM profile_reviews WHERE id=?",
            (review_id,),
        ).fetchone()


def update_profile_review(review_id: int, status: str) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE profile_reviews SET status=? WHERE id=?",
            (status, review_id),
        )


def set_profile_review_message(review_id: int, message_id: int) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE profile_reviews SET review_message_id=? WHERE id=?",
            (message_id, review_id),
        )


def all_pending_profile_reviews() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM profile_reviews WHERE status='pending'"
        ).fetchall()


def detect_profile_gender(profile_text: str) -> str:
    import re

    match = re.search(
        r"【性別/年齢】[ \t]*([^\n\r]*)",
        profile_text,
        flags=re.IGNORECASE,
    )
    value = match.group(1).strip() if match else ""

    if not value:
        lines = profile_text.splitlines()
        for i, line in enumerate(lines):
            if "【性別/年齢】" in line:
                remainder = line.split("【性別/年齢】", 1)[1].strip()
                if remainder:
                    value = remainder
                elif i + 1 < len(lines):
                    value = lines[i + 1].strip()
                break

    lowered = value.lower()
    if "女性" in value or value.startswith("女") or "female" in lowered:
        return "female"
    if "男性" in value or value.startswith("男") or "male" in lowered:
        return "male"
    return ""


# =========================================================
# 共通
# =========================================================

@dataclass(slots=True)
class RoomSpec:
    name: str
    category_id: int
    room_type: str
    public_view: bool
    hidden_when_two: bool = False
    timed: bool = False
    user_limit: int = 0
    opposite_gender_only: bool = False
    public_connect: bool = False


def clean_channel_name(text: str) -> str:
    for char in "/\\#,:`":
        text = text.replace(char, " ")
    return " ".join(text.split())[:90] or "個室"


def is_bot_admin(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or bool(BOT_ADMIN_ROLE_ID and member.get_role(BOT_ADMIN_ROLE_ID))
    )


def get_text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def get_category(guild: discord.Guild, category_id: int) -> Optional[discord.CategoryChannel]:
    channel = guild.get_channel(category_id)
    return channel if isinstance(channel, discord.CategoryChannel) else None


def profile_channel_for(member: discord.Member) -> Optional[discord.TextChannel]:
    if member.get_role(MALE_ROLE_ID):
        return get_text_channel(member.guild, MALE_PROFILE_CHANNEL_ID)
    if member.get_role(FEMALE_ROLE_ID):
        return get_text_channel(member.guild, FEMALE_PROFILE_CHANNEL_ID)
    return None


def target_role_and_label(guild: discord.Guild, target_type: str) -> tuple[str, str]:
    male = guild.get_role(MALE_NOTIFY_ROLE_ID)
    female = guild.get_role(FEMALE_NOTIFY_ROLE_ID)

    if target_type == "male":
        return (male.mention if male else "", "男性宛")
    if target_type == "female":
        return (female.mention if female else "", "女性宛")

    mentions = " ".join(
        role.mention for role in (male, female) if role is not None
    )
    return mentions, "全員宛"


async def send_log(guild: discord.Guild, text: str) -> None:
    channel = get_text_channel(guild, RECRUIT_LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(text)
        except discord.HTTPException:
            log.exception("ログ送信失敗")



def quick_role_connect_settings(
    room_type: str,
    owner: discord.Member,
) -> tuple[bool, bool]:
    """
    戻り値: (男性ロールが接続可能か, 女性ロールが接続可能か)
    クイック作成部屋は男女ロールから必ず見えるようにし、
    接続可否だけを部屋タイプに合わせて制御します。
    """
    # マイリスト部屋は表示のみ。接続は選択されたメンバーだけ。
    if "mylist" in room_type:
        return False, False

    # ノック部屋は表示のみ。承認後に個別で接続権限を付ける。
    if "knock" in room_type:
        return False, False

    owner_is_male = owner.get_role(MALE_ROLE_ID) is not None
    owner_is_female = owner.get_role(FEMALE_ROLE_ID) is not None

    # 同性NG
    same_gender_ng = (
        room_type.startswith("private_qm_ng_")
        or room_type in {"sleep_normal_ng", "sleep_knock_ng"}
    )
    if same_gender_ng:
        if owner_is_male:
            return False, True
        if owner_is_female:
            return True, False
        return True, True

    # それ以外のクイック作成部屋は男女とも接続可能
    return True, True


def is_quick_room_type(room_type: str) -> bool:
    return room_type.startswith(
        (
            "public_mylist",
            "private_mylist",
            "public_qm_",
            "public_timed",
            "private_timed",
            "sleep_",
            "eroip_",
            "private_qm_",
        )
    )


async def apply_quick_role_visibility(
    channel: discord.VoiceChannel,
    owner: discord.Member,
    room_type: str,
) -> None:
    """
    既存部屋にも使える権限補正。
    男性・女性ロールはクイック部屋を必ず閲覧できます。
    """
    if not is_quick_room_type(room_type):
        return

    male_role = channel.guild.get_role(MALE_ROLE_ID)
    female_role = channel.guild.get_role(FEMALE_ROLE_ID)
    male_connect, female_connect = quick_role_connect_settings(room_type, owner)

    if male_role:
        await channel.set_permissions(
            male_role,
            view_channel=True,
            connect=male_connect,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
            reason="クイック作成部屋を男性ロールから表示",
        )

    if female_role:
        await channel.set_permissions(
            female_role,
            view_channel=True,
            connect=female_connect,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
            reason="クイック作成部屋を女性ロールから表示",
        )


async def create_room(
    interaction: discord.Interaction,
    spec: RoomSpec,
    *,
    allowed_members: Optional[list[discord.Member]] = None,
) -> Optional[discord.VoiceChannel]:
    guild = interaction.guild
    owner = interaction.user

    if guild is None or not isinstance(owner, discord.Member):
        await interaction.followup.send("サーバー内で使用してください。", ephemeral=True)
        return None

    category = get_category(guild, spec.category_id)
    if category is None:
        await interaction.followup.send(
            f"作成先カテゴリ `{spec.category_id}` が見つかりません。",
            ephemeral=True,
        )
        return None

    everyone = guild.default_role
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        everyone: discord.PermissionOverwrite(
            view_channel=spec.public_view,
            connect=spec.public_connect,
            speak=spec.public_connect,
            stream=spec.public_connect,
            use_voice_activation=spec.public_connect,
            send_messages=spec.public_view,
            read_message_history=spec.public_view,
            use_application_commands=spec.public_view,
        ),
        owner: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
            manage_channels=True,
            move_members=True,
        ),
    }

    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            move_members=True,
            manage_channels=True,
            manage_permissions=True,
        )

    for member in allowed_members or []:
        if member.bot or member.id == owner.id or is_blocked_either_way(owner.id, member.id):
            continue
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
        )

    # クイック作成部屋は、男性・女性ロールから必ず見えるようにする
    if is_quick_room_type(spec.room_type):
        male_role = guild.get_role(MALE_ROLE_ID)
        female_role = guild.get_role(FEMALE_ROLE_ID)
        male_connect, female_connect = quick_role_connect_settings(spec.room_type, owner)

        if male_role:
            overwrites[male_role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=male_connect,
                send_messages=True,
                read_message_history=True,
                use_application_commands=True,
            )
        if female_role:
            overwrites[female_role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=female_connect,
                send_messages=True,
                read_message_history=True,
                use_application_commands=True,
            )

    if spec.opposite_gender_only:
        male = guild.get_role(MALE_ROLE_ID)
        female = guild.get_role(FEMALE_ROLE_ID)
        if male and female:
            if male in owner.roles:
                overwrites[female] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=("knock" not in spec.room_type),
                    send_messages=True,
                    read_message_history=True,
                    use_application_commands=True,
                )
                overwrites[male] = discord.PermissionOverwrite(view_channel=True, connect=False)
            elif female in owner.roles:
                overwrites[male] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=("knock" not in spec.room_type),
                    send_messages=True,
                    read_message_history=True,
                    use_application_commands=True,
                )
                overwrites[female] = discord.PermissionOverwrite(view_channel=True, connect=False)

    for blocked_id in set(get_pairs("blacklist", owner.id)):
        blocked = guild.get_member(blocked_id)
        if blocked:
            overwrites[blocked] = discord.PermissionOverwrite(view_channel=False, connect=False)

    try:
        channel = await guild.create_voice_channel(
            name=clean_channel_name(spec.name),
            category=category,
            overwrites=overwrites,
            user_limit=spec.user_limit,
            reason=f"NOIR Bot: {owner} が {spec.room_type} を作成",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "Botにチャンネル管理・権限管理・メンバー移動の権限を付けてください。",
            ephemeral=True,
        )
        return None
    except discord.HTTPException as exc:
        await interaction.followup.send(f"作成失敗: `{exc}`", ephemeral=True)
        return None

    save_room(
        channel.id,
        guild.id,
        owner.id,
        spec.room_type,
        spec.hidden_when_two,
        spec.timed,
    )

    # カテゴリ側の権限に関係なく、男女ロールへ明示的に表示権限を付ける
    try:
        await apply_quick_role_visibility(channel, owner, spec.room_type)
    except discord.Forbidden:
        log.warning("クイック部屋の男女ロール権限を設定できません: %s", channel.id)
    except discord.HTTPException:
        log.exception("クイック部屋権限設定失敗: %s", channel.id)

    # ノックあり部屋は通常テキストチャンネルへノックパネルを設置
    try:
        await post_external_knock_panel(channel, owner, spec.room_type)
    except discord.Forbidden:
        log.warning("外部ノックパネルを投稿できません: %s", channel.id)
    except discord.HTTPException:
        log.exception("外部ノックパネル投稿失敗: %s", channel.id)

    # VC内チャットへ管理メニューを自動設置
    try:
        menu_embed = build_vc_menu_embed(channel, owner, spec.room_type)
        await channel.send(embed=menu_embed, view=VCMenuView())
    except discord.Forbidden:
        log.warning("VC内チャットへ管理メニューを送信できません: %s", channel.id)
    except discord.HTTPException:
        log.exception("VC管理メニュー送信失敗: %s", channel.id)

    await interaction.followup.send(f"✅ {channel.mention} を作成しました。", ephemeral=True)
    return channel




# =========================================================
# 外部ノックパネル
# =========================================================

def build_external_knock_embed(
    channel: discord.VoiceChannel,
    owner: discord.Member,
    room_type: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="🚪 ノック受付中",
        description=(
            f"**部屋**\n{channel.mention}\n\n"
            f"**部屋主**\n{owner.mention}\n\n"
            "下のボタンからノックできます。\n"
            "承認されるまではVCへ接続できません。"
        ),
        color=discord.Color.from_rgb(150, 100, 210),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="部屋タイプ", value=room_type, inline=False)
    embed.set_thumbnail(url=owner.display_avatar.url)
    embed.set_footer(text=f"VC ID: {channel.id}")
    return embed


async def disable_external_knock_panel(
    bot: commands.Bot,
    guild: discord.Guild,
    row: sqlite3.Row,
    *,
    reason_text: str = "この部屋は終了しました",
) -> None:
    message_id = int(row["knock_message_id"]) if "knock_message_id" in row.keys() else 0
    if not message_id:
        return

    panel_channel = get_text_channel(guild, KNOCK_PANEL_CHANNEL_ID)
    if panel_channel is None:
        return

    try:
        message = await panel_channel.fetch_message(message_id)
        embed = message.embeds[0] if message.embeds else discord.Embed(title="🚪 ノック受付")
        embed.color = discord.Color.dark_grey()
        embed.set_footer(text=reason_text)
        await message.edit(embed=embed, view=None)
    except (discord.NotFound, discord.Forbidden):
        pass
    except discord.HTTPException:
        log.exception("外部ノックパネル終了処理失敗: %s", message_id)


class KnockDMDecisionView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.request_id = request_id

        approve = discord.ui.Button(
            label="入室許可",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"noir:knock_dm:approve:{request_id}",
        )
        reject = discord.ui.Button(
            label="お断り",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"noir:knock_dm:reject:{request_id}",
        )
        approve.callback = self.approve
        reject.callback = self.reject
        self.add_item(approve)
        self.add_item(reject)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        req = get_knock_request(self.request_id)
        if req is None:
            await interaction.response.send_message(
                "ノック情報が見つかりません。",
                ephemeral=True,
            )
            return False

        if req["status"] != "pending":
            await interaction.response.send_message(
                "このノックはすでに処理済みです。",
                ephemeral=True,
            )
            return False

        if interaction.user.id != int(req["owner_id"]):
            await interaction.response.send_message(
                "部屋主だけ操作できます。",
                ephemeral=True,
            )
            return False
        return True

    async def approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        req = get_knock_request(self.request_id)
        if req is None or req["status"] != "pending":
            await interaction.followup.send(
                "このノックはすでに処理済みです。",
                ephemeral=True,
            )
            return

        guild = bot.get_guild(int(req["guild_id"]))
        channel = (
            guild.get_channel(int(req["channel_id"]))
            if guild is not None
            else None
        )
        applicant = (
            guild.get_member(int(req["applicant_id"]))
            if guild is not None
            else None
        )

        if guild is None or not isinstance(channel, discord.VoiceChannel):
            update_knock_request(self.request_id, "expired")
            await interaction.followup.send(
                "対象の部屋はすでに終了しています。",
                ephemeral=True,
            )
            return

        if applicant is None:
            update_knock_request(self.request_id, "expired")
            await interaction.followup.send(
                "ノックしたメンバーが見つかりません。",
                ephemeral=True,
            )
            return

        if is_blocked_either_way(int(req["owner_id"]), applicant.id):
            await interaction.followup.send(
                "ブラックリスト関係のため許可できません。",
                ephemeral=True,
            )
            return

        try:
            await channel.set_permissions(
                applicant,
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
                send_messages=True,
                read_message_history=True,
                use_application_commands=True,
                reason="DMからノック承認",
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("ノック承認の権限付与失敗")
            await interaction.followup.send(
                "VCの入室権限を付与できませんでした。",
                ephemeral=True,
            )
            return

        update_knock_request(self.request_id, "approved")

        try:
            await applicant.send(
                f"✅ **{guild.name}** の **{channel.name}** へのノックが承認されました。\n"
                f"サーバーに戻って {channel.mention} へ入室できます。"
            )
        except discord.HTTPException:
            pass

        if interaction.message:
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else discord.Embed(title="🚪 ノックが届きました")
                )
                embed.color = discord.Color.green()
                embed.add_field(
                    name="結果",
                    value=f"✅ 入室許可済み：{applicant}",
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            "✅ 入室を許可しました。ノックした人にもDM通知しました。",
            ephemeral=True,
        )

    async def reject(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        req = get_knock_request(self.request_id)
        if req is None or req["status"] != "pending":
            await interaction.followup.send(
                "このノックはすでに処理済みです。",
                ephemeral=True,
            )
            return

        guild = bot.get_guild(int(req["guild_id"]))
        applicant = (
            guild.get_member(int(req["applicant_id"]))
            if guild is not None
            else None
        )
        update_knock_request(self.request_id, "rejected")

        if applicant and guild:
            try:
                await applicant.send(
                    f"❌ **{guild.name}** のノックは今回はお断りとなりました。"
                )
            except discord.HTTPException:
                pass

        if interaction.message:
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else discord.Embed(title="🚪 ノックが届きました")
                )
                embed.color = discord.Color.red()
                embed.add_field(
                    name="結果",
                    value="❌ お断り済み",
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            "❌ ノックをお断りしました。相手にもDM通知しました。",
            ephemeral=True,
        )


class ExternalKnockPanelView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        knock_button = discord.ui.Button(
            label="ノックする",
            emoji="🚪",
            style=discord.ButtonStyle.primary,
            custom_id=f"noir:external_knock:{channel_id}",
        )
        knock_button.callback = self.knock
        self.add_item(knock_button)

    async def knock(self, interaction: discord.Interaction) -> None:
        row = get_room(self.channel_id)
        guild = interaction.guild
        channel = guild.get_channel(self.channel_id) if guild else None

        if row is None or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "この部屋はすでに終了しています。",
                ephemeral=True,
            )
            return

        owner_id = int(row["owner_id"])
        if interaction.user.id == owner_id:
            await interaction.response.send_message(
                "部屋主本人はノック不要です。",
                ephemeral=True,
            )
            return

        if is_blocked_either_way(owner_id, interaction.user.id):
            await interaction.response.send_message(
                "ブラックリスト関係のためノックできません。",
                ephemeral=True,
            )
            return

        owner = guild.get_member(owner_id)
        panel_channel = get_text_channel(guild, KNOCK_PANEL_CHANNEL_ID)
        if panel_channel is None:
            await interaction.response.send_message(
                "ノック通知チャンネルが見つかりません。",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🚪 ノックが届きました",
            description=(
                f"{interaction.user.mention} さんが\n"
                f"{channel.mention} へノックしました。"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        request_id = create_knock_request(
            guild.id,
            channel.id,
            owner_id,
            interaction.user.id,
        )
        decision_view = KnockDMDecisionView(request_id)

        await panel_channel.send(
            content=owner.mention if owner else f"<@{owner_id}>",
            embed=embed,
            view=decision_view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        if owner:
            try:
                dm_embed = discord.Embed(
                    title="🚪 ノックが届きました",
                    description=(
                        f"**ノックした人**：{interaction.user.mention}\n"
                        f"**ユーザー名**：{interaction.user}\n"
                        f"**部屋**：{channel.name}\n\n"
                        "下のボタンから入室許可またはお断りを選択できます。"
                    ),
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc),
                )
                dm_embed.set_thumbnail(url=interaction.user.display_avatar.url)
                dm_message = await owner.send(
                    embed=dm_embed,
                    view=KnockDMDecisionView(request_id),
                )
                set_knock_dm_message_id(request_id, dm_message.id)
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            "🚪 ノックを送りました。入室許可をお待ちください。",
            ephemeral=True,
        )


async def post_external_knock_panel(
    channel: discord.VoiceChannel,
    owner: discord.Member,
    room_type: str,
) -> None:
    if "knock" not in room_type:
        return

    panel_channel = get_text_channel(channel.guild, KNOCK_PANEL_CHANNEL_ID)
    if panel_channel is None:
        log.error("ノックパネル投稿先が見つかりません: %s", KNOCK_PANEL_CHANNEL_ID)
        return

    message = await panel_channel.send(
        embed=build_external_knock_embed(channel, owner, room_type),
        view=ExternalKnockPanelView(channel.id),
    )
    set_knock_message_id(channel.id, message.id)


# =========================================================
# VC内チャット管理メニュー
# =========================================================

def get_interaction_voice_channel(
    interaction: discord.Interaction,
) -> Optional[discord.VoiceChannel]:
    return (
        interaction.channel
        if isinstance(interaction.channel, discord.VoiceChannel)
        else None
    )


def get_owned_room(
    interaction: discord.Interaction,
) -> tuple[Optional[discord.VoiceChannel], Optional[sqlite3.Row]]:
    channel = get_interaction_voice_channel(interaction)
    if channel is None:
        return None, None
    return channel, get_room(channel.id)


async def require_room_owner(
    interaction: discord.Interaction,
) -> tuple[Optional[discord.VoiceChannel], Optional[sqlite3.Row]]:
    channel, row = get_owned_room(interaction)
    if channel is None or row is None:
        await interaction.response.send_message(
            "このBotが作成したVC内で使用してください。",
            ephemeral=True,
        )
        return None, None

    member = interaction.user
    allowed = (
        interaction.user.id == int(row["owner_id"])
        or (isinstance(member, discord.Member) and is_bot_admin(member))
    )
    if not allowed:
        await interaction.response.send_message(
            "部屋の作成者または管理者だけ操作できます。",
            ephemeral=True,
        )
        return None, None

    return channel, row


def build_vc_menu_embed(
    channel: discord.VoiceChannel,
    owner: discord.Member,
    room_type: str,
) -> discord.Embed:
    knock_text = "ON" if "knock" in room_type else "OFF"
    timed_text = "10分制" if "timed" in room_type else "なし"

    embed = discord.Embed(
        title="VCメニュー",
        description=(
            "🍰 **名前／ステータス変更**\n"
            "部屋名とチャンネルステータスを変更できます。\n\n"
            "🌟 **ビットレート変更**\n"
            "VCの音質を変更できます。\n\n"
            "🛌 **寝落ち切断**\n"
            "指定時間後、入室中のメンバーを切断します。\n\n"
            "🐑 **権限確認**\n"
            "現在の部屋の閲覧・接続権限を確認できます。\n\n"
            "🚪 **ノック**\n"
            "ノック部屋では、作成者へ入室申請を送れます。\n\n"
            "🔒 **ロック／解除**\n"
            "新しい入室を止めたり、再開できます。\n\n"
            "🗑️ **部屋削除**\n"
            "作成したVCを閉じます。"
        ),
        color=discord.Color.from_rgb(238, 197, 135),
    )
    embed.add_field(name="👑 作成者", value=owner.mention, inline=True)
    embed.add_field(name="🚪 ノック", value=knock_text, inline=True)
    embed.add_field(name="⏰ タイマー", value=timed_text, inline=True)
    embed.set_footer(text=f"チャンネルID: {channel.id}")
    return embed


class VCNameStatusModal(discord.ui.Modal):
    def __init__(self, channel: discord.VoiceChannel):
        super().__init__(title="名前／ステータス変更")
        self.channel_id = channel.id

        self.room_name = discord.ui.TextInput(
            label="新しい部屋名",
            default=channel.name[:100],
            max_length=100,
        )
        self.status = discord.ui.TextInput(
            label="チャンネルステータス",
            placeholder="空欄の場合は変更しません",
            max_length=500,
            required=False,
        )
        self.add_item(self.room_name)
        self.add_item(self.status)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel, row = await require_room_owner(interaction)
        if channel is None or row is None:
            return

        new_name = clean_channel_name(str(self.room_name.value))
        try:
            await channel.edit(
                name=new_name,
                reason=f"{interaction.user} がVC名を変更",
            )

            status_text = str(self.status.value).strip()
            if status_text:
                setter = getattr(channel, "set_status", None)
                if setter:
                    try:
                        await setter(status=status_text[:500])
                    except Exception:
                        log.exception("VCステータス変更失敗: %s", channel.id)

            await interaction.response.send_message(
                f"✅ 部屋名を **{new_name}** に変更しました。",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Botにチャンネル管理権限がありません。",
                ephemeral=True,
            )


class VCBitrateModal(discord.ui.Modal):
    def __init__(self, channel: discord.VoiceChannel):
        super().__init__(title="ビットレート変更")
        self.channel_id = channel.id
        self.bitrate = discord.ui.TextInput(
            label="ビットレート（kbps）",
            placeholder="例：64",
            default=str(max(8, channel.bitrate // 1000)),
            min_length=1,
            max_length=3,
        )
        self.add_item(self.bitrate)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel, row = await require_room_owner(interaction)
        if channel is None or row is None:
            return

        try:
            requested = int(str(self.bitrate.value))
        except ValueError:
            await interaction.response.send_message(
                "数字だけを入力してください。",
                ephemeral=True,
            )
            return

        max_kbps = max(8, channel.guild.bitrate_limit // 1000)
        requested = max(8, min(requested, max_kbps))

        try:
            await channel.edit(
                bitrate=requested * 1000,
                reason=f"{interaction.user} がビットレートを変更",
            )
            await interaction.response.send_message(
                f"✅ ビットレートを **{requested}kbps** に変更しました。",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Botにチャンネル管理権限がありません。",
                ephemeral=True,
            )


class VCSleepDisconnectModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="寝落ち切断")
        self.minutes = discord.ui.TextInput(
            label="何分後に切断しますか？",
            placeholder="例：60",
            default="60",
            max_length=4,
        )
        self.add_item(self.minutes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel, row = await require_room_owner(interaction)
        if channel is None or row is None:
            return

        try:
            minutes = int(str(self.minutes.value))
        except ValueError:
            await interaction.response.send_message(
                "数字だけを入力してください。",
                ephemeral=True,
            )
            return

        if not 1 <= minutes <= 1440:
            await interaction.response.send_message(
                "1～1440分で指定してください。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🛌 **{minutes}分後**に、入室中のメンバーを切断します。",
            ephemeral=True,
        )

        async def disconnect_later() -> None:
            await asyncio.sleep(minutes * 60)
            current = channel.guild.get_channel(channel.id)
            if not isinstance(current, discord.VoiceChannel):
                return
            for member in list(current.members):
                if member.bot:
                    continue
                try:
                    await member.move_to(None, reason="寝落ち切断タイマー")
                except discord.HTTPException:
                    log.exception("寝落ち切断失敗: %s", member.id)

        asyncio.create_task(disconnect_later())


class KnockDecisionView(discord.ui.View):
    def __init__(self, channel_id: int, applicant_id: int):
        super().__init__(timeout=15 * 60)
        self.channel_id = channel_id
        self.applicant_id = applicant_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        row = get_room(self.channel_id)
        if row is None:
            await interaction.response.send_message(
                "部屋がすでに削除されています。",
                ephemeral=True,
            )
            return False

        member = interaction.user
        if interaction.user.id != int(row["owner_id"]) and not (
            isinstance(member, discord.Member) and is_bot_admin(member)
        ):
            await interaction.response.send_message(
                "部屋の作成者または管理者だけ操作できます。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="入室許可",
        emoji="✅",
        style=discord.ButtonStyle.success,
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        guild = interaction.guild
        channel = guild.get_channel(self.channel_id) if guild else None
        applicant = guild.get_member(self.applicant_id) if guild else None

        if not isinstance(channel, discord.VoiceChannel) or applicant is None:
            await interaction.response.send_message(
                "対象の部屋またはユーザーが見つかりません。",
                ephemeral=True,
            )
            return

        if is_blocked_either_way(int(get_room(channel.id)["owner_id"]), applicant.id):
            await interaction.response.send_message(
                "ブラックリスト関係のため許可できません。",
                ephemeral=True,
            )
            return

        await channel.set_permissions(
            applicant,
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
            reason="ノック承認",
        )
        try:
            await applicant.send(
                f"✅ **{guild.name}** の {channel.mention} へのノックが承認されました。"
            )
        except discord.HTTPException:
            pass

        await interaction.response.edit_message(
            content=f"✅ {applicant.mention} の入室を許可しました。",
            view=None,
        )

    @discord.ui.button(
        label="お断り",
        emoji="❌",
        style=discord.ButtonStyle.danger,
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        guild = interaction.guild
        applicant = guild.get_member(self.applicant_id) if guild else None
        if applicant:
            try:
                await applicant.send(
                    f"今回はノックが見送られました。"
                )
            except discord.HTTPException:
                pass

        await interaction.response.edit_message(
            content=f"❌ <@{self.applicant_id}> のノックをお断りしました。",
            view=None,
        )


class VCMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="名前／ステータス変更",
        emoji="🍰",
        style=discord.ButtonStyle.secondary,
        custom_id="noir:vc:name_status",
        row=0,
    )
    async def name_status(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel, row = get_owned_room(interaction)
        if channel is None or row is None:
            await interaction.response.send_message(
                "このBotが作成したVC内で使用してください。",
                ephemeral=True,
            )
            return

        member = interaction.user
        if interaction.user.id != int(row["owner_id"]) and not (
            isinstance(member, discord.Member) and is_bot_admin(member)
        ):
            await interaction.response.send_message(
                "部屋の作成者または管理者だけ操作できます。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(VCNameStatusModal(channel))

    @discord.ui.button(
        label="ビットレート変更",
        emoji="🌟",
        style=discord.ButtonStyle.secondary,
        custom_id="noir:vc:bitrate",
        row=0,
    )
    async def bitrate(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel, row = get_owned_room(interaction)
        if channel is None or row is None:
            await interaction.response.send_message(
                "このBotが作成したVC内で使用してください。",
                ephemeral=True,
            )
            return

        member = interaction.user
        if interaction.user.id != int(row["owner_id"]) and not (
            isinstance(member, discord.Member) and is_bot_admin(member)
        ):
            await interaction.response.send_message(
                "部屋の作成者または管理者だけ操作できます。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(VCBitrateModal(channel))

    @discord.ui.button(
        label="寝落ち切断",
        emoji="🛌",
        style=discord.ButtonStyle.secondary,
        custom_id="noir:vc:sleep_disconnect",
        row=1,
    )
    async def sleep_disconnect(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel, row = get_owned_room(interaction)
        if channel is None or row is None:
            await interaction.response.send_message(
                "このBotが作成したVC内で使用してください。",
                ephemeral=True,
            )
            return

        member = interaction.user
        if interaction.user.id != int(row["owner_id"]) and not (
            isinstance(member, discord.Member) and is_bot_admin(member)
        ):
            await interaction.response.send_message(
                "部屋の作成者または管理者だけ操作できます。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(VCSleepDisconnectModal())

    @discord.ui.button(
        label="権限確認",
        emoji="🐑",
        style=discord.ButtonStyle.secondary,
        custom_id="noir:vc:permissions",
        row=1,
    )
    async def permissions(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel, row = get_owned_room(interaction)
        if channel is None or row is None:
            await interaction.response.send_message(
                "このBotが作成したVC内で使用してください。",
                ephemeral=True,
            )
            return

        visible_members = []
        connect_members = []
        for member in channel.guild.members:
            if member.bot:
                continue
            perms = channel.permissions_for(member)
            if perms.view_channel:
                visible_members.append(member.display_name)
            if perms.connect:
                connect_members.append(member.display_name)

        visible_text = "、".join(visible_members[:30]) or "なし"
        connect_text = "、".join(connect_members[:30]) or "なし"
        if len(visible_members) > 30:
            visible_text += f" ほか{len(visible_members) - 30}名"
        if len(connect_members) > 30:
            connect_text += f" ほか{len(connect_members) - 30}名"

        await interaction.response.send_message(
            f"👀 **閲覧可能**\n{visible_text}\n\n"
            f"🎙️ **接続可能**\n{connect_text}",
            ephemeral=True,
        )

    @discord.ui.button(
        label="ノック案内",
        emoji="🚪",
        style=discord.ButtonStyle.primary,
        custom_id="noir:vc:knock",
        row=2,
    )
    async def knock(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel, row = get_owned_room(interaction)
        if channel is None or row is None:
            await interaction.response.send_message(
                "このBotが作成したVC内で使用してください。",
                ephemeral=True,
            )
            return

        if "knock" not in str(row["room_type"]):
            await interaction.response.send_message(
                "この部屋はノック部屋ではありません。",
                ephemeral=True,
            )
            return

        panel_channel = get_text_channel(channel.guild, KNOCK_PANEL_CHANNEL_ID)
        if panel_channel is None:
            await interaction.response.send_message(
                "ノック受付チャンネルが見つかりません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🚪 ノックは {panel_channel.mention} の受付パネルから送れます。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="ロック／解除",
        emoji="🔒",
        style=discord.ButtonStyle.primary,
        custom_id="noir:vc:lock_toggle",
        row=2,
    )
    async def lock_toggle(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel, row = await require_room_owner(interaction)
        if channel is None or row is None:
            return

        everyone = channel.guild.default_role
        current = channel.overwrites_for(everyone)
        currently_locked = current.connect is False

        # 表示は維持しつつ接続だけ切り替える
        current.connect = True if currently_locked else False
        await channel.set_permissions(
            everyone,
            overwrite=current,
            reason=f"{interaction.user} がロック切替",
        )

        # 性別ロールに明示許可がある場合も合わせて切替
        for role_id in (MALE_ROLE_ID, FEMALE_ROLE_ID):
            role = channel.guild.get_role(role_id)
            if role is None:
                continue
            overwrite = channel.overwrites_for(role)
            if overwrite.view_channel is True:
                overwrite.connect = (
                    False
                    if not currently_locked
                    else ("knock" not in str(row["room_type"]))
                )
                await channel.set_permissions(
                    role,
                    overwrite=overwrite,
                    reason=f"{interaction.user} がロック切替",
                )

        await interaction.response.send_message(
            "🔓 ロックを解除しました。"
            if currently_locked
            else "🔒 部屋をロックしました。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="部屋削除",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="noir:vc:delete",
        row=2,
    )
    async def delete_room(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel, row = await require_room_owner(interaction)
        if channel is None or row is None:
            return

        await interaction.response.send_message(
            "🗑️ 部屋を削除します。",
            ephemeral=True,
        )
        try:
            await disable_external_knock_panel(
                interaction.client,
                channel.guild,
                row,
                reason_text="部屋主がこの部屋を終了しました",
            )
            await channel.delete(reason=f"{interaction.user} がVCメニューから削除")
        finally:
            delete_room_record(channel.id)


# =========================================================
# VCパネル
# =========================================================

class QuickChoiceSelect(discord.ui.Select):
    def __init__(self, kind: str):
        self.kind = kind
        choices = {
            "public_mylist": [("通常部屋", "normal", "🌱")],
            "private_mylist": [
                ("通常部屋", "normal", "📚"),
                ("ノック部屋", "knock", "🚪"),
                ("添い寝部屋", "sleep", "💤"),
                ("ノック添い寝部屋", "knock_sleep", "🌙"),
            ],
            "public_qm": [
                ("新規開拓", "new", "🌱"),
                ("作業", "work", "🛠️"),
                ("ゲーム", "game", "🎮"),
            ],
            "sleep": [
                ("通常・同性OK", "normal_ok", "💤"),
                ("通常・同性NG", "normal_ng", "🌙"),
                ("ノック・同性OK", "knock_ok", "🚪"),
                ("ノック・同性NG", "knock_ng", "🔒"),
            ],
            "eroip": [
                ("待機（ノックなし）", "normal", "🔞"),
                ("ノックあり", "knock", "🚪"),
            ],
            "private_qm": [
                ("同性OK・ノックなし", "ok_normal", "🌙"),
                ("同性NG・ノックなし", "ng_normal", "🌙"),
                ("同性OK・ノックあり", "ok_knock", "🚪"),
                ("同性NG・ノックあり", "ng_knock", "🔒"),
            ],
        }
        options = [
            discord.SelectOption(label=label, value=value, emoji=emoji)
            for label, value, emoji in choices[kind]
        ]
        super().__init__(
            placeholder="部屋タイプを選択してください",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            return
        value = self.values[0]
        name = member.display_name

        if self.kind in {"public_mylist", "private_mylist"}:
            members = [
                interaction.guild.get_member(uid)
                for uid in get_pairs("favorites", member.id)
                if interaction.guild and interaction.guild.get_member(uid)
            ]
            members = [m for m in members if m and not is_blocked_either_way(member.id, m.id)]
            if not members:
                await interaction.followup.send(
                    "マイリストに招待可能なメンバーがいません。",
                    ephemeral=True,
                )
                return
            hidden = self.kind == "private_mylist"
            await create_room(
                interaction,
                RoomSpec(
                    name=f"{'📚' if hidden else '🌱'}｜{name}のマイリスト",
                    category_id=QUICK_PRIVATE_CATEGORY_ID if hidden else QUICK_PUBLIC_CATEGORY_ID,
                    room_type=f"{self.kind}_{value}",
                    public_view=False,
                    hidden_when_two=hidden,
                    user_limit=0,
                ),
                allowed_members=members,
            )
            return

        if self.kind == "public_qm":
            labels = {"new": "新規開拓", "work": "作業", "game": "ゲーム"}
            await create_room(
                interaction,
                RoomSpec(
                    name=f"🌱｜{labels[value]}｜{name}",
                    category_id=QUICK_PUBLIC_CATEGORY_ID,
                    room_type=f"public_qm_{value}",
                    public_view=True,
                    user_limit=0,
                ),
            )
            return

        if self.kind == "sleep":
            knock = value.startswith("knock")
            same_ok = value.endswith("_ok")
            await create_room(
                interaction,
                RoomSpec(
                    name=f"💤｜{'ノック' if knock else '通常'}・{'同性OK' if same_ok else '同性NG'}｜{name}",
                    category_id=QUICK_PRIVATE_CATEGORY_ID,
                    room_type=f"sleep_{value}",
                    public_view=True,
                    hidden_when_two=True,
                    user_limit=2,
                    opposite_gender_only=not same_ok,
                ),
            )
            return

        if self.kind == "eroip":
            await create_room(
                interaction,
                RoomSpec(
                    name=f"🔞｜{'ノック' if value == 'knock' else '待機'}｜{name}",
                    category_id=QUICK_PRIVATE_CATEGORY_ID,
                    room_type=f"eroip_{value}",
                    public_view=True,
                    hidden_when_two=True,
                    user_limit=2,
                ),
            )
            return

        if self.kind == "private_qm":
            same_ok = value.startswith("ok")
            await create_room(
                interaction,
                RoomSpec(
                    name=f"🌙｜{'同性OK' if same_ok else '同性NG'}｜{name}",
                    category_id=QUICK_PRIVATE_CATEGORY_ID,
                    room_type=f"private_qm_{value}",
                    public_view=False,
                    hidden_when_two=True,
                    user_limit=2,
                    opposite_gender_only=not same_ok,
                ),
            )


class QuickChoiceView(discord.ui.View):
    def __init__(self, kind: str):
        super().__init__(timeout=120)
        self.add_item(QuickChoiceSelect(kind))


class QuickPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def open_select(self, interaction: discord.Interaction, kind: str, text: str) -> None:
        await interaction.response.send_message(
            text,
            view=QuickChoiceView(kind),
            ephemeral=True,
        )

    @discord.ui.button(label="表マイリスト部屋", emoji="🌱", style=discord.ButtonStyle.success, custom_id="noir:quick:public_mylist", row=0)
    async def public_mylist(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_select(interaction, "public_mylist", "作成タイプを選択してください。")

    @discord.ui.button(label="裏マイリスト部屋", emoji="📚", style=discord.ButtonStyle.primary, custom_id="noir:quick:private_mylist", row=0)
    async def private_mylist(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_select(interaction, "private_mylist", "作成タイプを選択してください。")

    @discord.ui.button(label="表QM部屋", emoji="🌱", style=discord.ButtonStyle.success, custom_id="noir:quick:public_qm", row=1)
    async def public_qm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_select(interaction, "public_qm", "表QMのタイプを選択してください。")

    @discord.ui.button(label="表時間制部屋", emoji="⏰", style=discord.ButtonStyle.secondary, custom_id="noir:quick:public_timed", row=1)
    async def public_timed(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        assert isinstance(member, discord.Member)
        await create_room(
            interaction,
            RoomSpec(
                name=f"⏰｜表時間制｜{member.display_name}",
                category_id=QUICK_PUBLIC_CATEGORY_ID,
                room_type="public_timed",
                public_view=True,
                timed=True,
                user_limit=2,
            ),
        )

    @discord.ui.button(label="裏時間制部屋", emoji="⏰", style=discord.ButtonStyle.secondary, custom_id="noir:quick:private_timed", row=1)
    async def private_timed(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        assert isinstance(member, discord.Member)
        await create_room(
            interaction,
            RoomSpec(
                name=f"⏰｜裏時間制｜{member.display_name}",
                category_id=QUICK_PRIVATE_CATEGORY_ID,
                room_type="private_timed",
                public_view=True,
                hidden_when_two=True,
                timed=True,
                user_limit=2,
            ),
        )

    @discord.ui.button(label="裏添い寝部屋", emoji="💤", style=discord.ButtonStyle.primary, custom_id="noir:quick:sleep", row=2)
    async def sleep(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_select(interaction, "sleep", "添い寝部屋のタイプを選択してください。")

    @discord.ui.button(label="エロイプ部屋", emoji="🔞", style=discord.ButtonStyle.danger, custom_id="noir:quick:eroip", row=2)
    async def eroip(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_select(interaction, "eroip", "部屋タイプを選択してください。")

    @discord.ui.button(label="裏QM部屋", emoji="🌙", style=discord.ButtonStyle.primary, custom_id="noir:quick:private_qm", row=2)
    async def private_qm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_select(interaction, "private_qm", "裏QMのタイプを選択してください。")



# =========================================================
# 公開エロイプ・ラジオ部屋
# =========================================================

async def create_public_special_room(
    interaction: discord.Interaction,
    *,
    kind: str,
    access_mode: str,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild
    owner = interaction.user
    if guild is None or not isinstance(owner, discord.Member):
        await interaction.followup.send(
            "サーバー内で使用してください。",
            ephemeral=True,
        )
        return

    allowed_members: list[discord.Member] = []
    public_connect = access_mode == "blacklist"

    if access_mode == "mylist":
        for user_id in get_pairs("favorites", owner.id):
            member = guild.get_member(user_id)
            if (
                member
                and not member.bot
                and member.id != owner.id
                and not is_blocked_either_way(owner.id, member.id)
            ):
                allowed_members.append(member)

        if not allowed_members:
            await interaction.followup.send(
                "マイリストに登録されているメンバーがいません。\n"
                "先に `/mylist_add` で登録してください。",
                ephemeral=True,
            )
            return

    if kind == "eroip":
        title = "公開エロイプ"
        emoji = "🔞"
        room_type = f"public_eroip_{access_mode}"
    else:
        title = "ラジオ"
        emoji = "📻"
        room_type = f"radio_{access_mode}"

    mode_label = "マイリスト" if access_mode == "mylist" else "ブラックリスト"
    await create_room(
        interaction,
        RoomSpec(
            name=f"{emoji}｜{title}｜{mode_label}｜{owner.display_name}",
            category_id=QUICK_PUBLIC_CATEGORY_ID,
            room_type=room_type,
            public_view=True,
            public_connect=public_connect,
            user_limit=0,
        ),
        allowed_members=allowed_members,
    )


class PublicSpecialRoomPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="マイリストで作成",
        emoji="🐑",
        style=discord.ButtonStyle.primary,
        custom_id="noir:public_special:eroip:mylist",
        row=0,
    )
    async def eroip_mylist(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await create_public_special_room(
            interaction,
            kind="eroip",
            access_mode="mylist",
        )

    @discord.ui.button(
        label="ブラックリストで作成",
        emoji="🚫",
        style=discord.ButtonStyle.secondary,
        custom_id="noir:public_special:eroip:blacklist",
        row=0,
    )
    async def eroip_blacklist(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await create_public_special_room(
            interaction,
            kind="eroip",
            access_mode="blacklist",
        )

    @discord.ui.button(
        label="マイリストで作成",
        emoji="🐑",
        style=discord.ButtonStyle.primary,
        custom_id="noir:public_special:radio:mylist",
        row=1,
    )
    async def radio_mylist(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await create_public_special_room(
            interaction,
            kind="radio",
            access_mode="mylist",
        )

    @discord.ui.button(
        label="ブラックリストで作成",
        emoji="🚫",
        style=discord.ButtonStyle.secondary,
        custom_id="noir:public_special:radio:blacklist",
        row=1,
    )
    async def radio_blacklist(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await create_public_special_room(
            interaction,
            kind="radio",
            access_mode="blacklist",
        )



class PrivateUserSelect(discord.ui.UserSelect):
    def __init__(self, hidden: bool):
        self.hidden = hidden
        super().__init__(
            placeholder="招待するユーザーを選択",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        owner = interaction.user
        assert isinstance(owner, discord.Member)

        members = []
        for user in self.values:
            member = interaction.guild.get_member(user.id) if interaction.guild else None
            if member and not member.bot and member.id != owner.id and not is_blocked_either_way(owner.id, member.id):
                members.append(member)

        if not members:
            await interaction.followup.send("招待可能なユーザーがいません。", ephemeral=True)
            return

        await create_room(
            interaction,
            RoomSpec(
                name=f"{'🚫' if self.hidden else '⭕'}｜{owner.display_name}の個室",
                category_id=PRIVATE_HIDDEN_CATEGORY_ID if self.hidden else PRIVATE_PUBLIC_CATEGORY_ID,
                room_type="hidden_private" if self.hidden else "public_private",
                public_view=not self.hidden,
                hidden_when_two=self.hidden,
                user_limit=2,
            ),
            allowed_members=members,
        )


class PrivateSelectView(discord.ui.View):
    def __init__(self, hidden: bool):
        super().__init__(timeout=120)
        self.add_item(PrivateUserSelect(hidden))


class PrivatePanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="表個室", emoji="⭕", style=discord.ButtonStyle.success, custom_id="noir:private:public")
    async def public_private(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "招待するユーザーを選択してください。",
            view=PrivateSelectView(False),
            ephemeral=True,
        )

    @discord.ui.button(label="裏個室", emoji="🚫", style=discord.ButtonStyle.danger, custom_id="noir:private:hidden")
    async def hidden_private(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "招待するユーザーを選択してください。",
            view=PrivateSelectView(True),
            ephemeral=True,
        )


# =========================================================
# なう募集
# =========================================================

class NowRecruitModal(discord.ui.Modal):
    def __init__(self, target_type: str):
        super().__init__(title="なう募集を作成")
        self.target_type = target_type
        self.comment = discord.ui.TextInput(
            label="一言",
            placeholder="例：誰か話そう！",
            default="誰か話そう！",
            max_length=200,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("サーバー内で使用してください。", ephemeral=True)
            return

        channel = get_text_channel(guild, NOW_RECRUIT_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message("なう募集チャンネルが見つかりません。", ephemeral=True)
            return

        mention, label = target_role_and_label(guild, self.target_type)
        profile_channel = profile_channel_for(member)
        profile_text = profile_channel.mention if profile_channel else "プロフィールチャンネル未設定"

        embed = discord.Embed(
            title=f"なう募集 - {label}",
            description=str(self.comment.value),
            color=discord.Color.from_rgb(255, 120, 180),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="ユーザー", value=f"{member.mention}\n`@{member.display_name}`", inline=False)
        embed.add_field(name="プロフィール", value=profile_text, inline=False)
        embed.add_field(name="一言", value=str(self.comment.value), inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="💞 立候補ボタンから応募できます")

        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await channel.send(
            content=mention or None,
            embed=embed,
            view=RecruitActionView(),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
        save_now_recruitment(message.id, member.id, self.target_type, str(self.comment.value))
        await interaction.followup.send(
            f"✅ {message.jump_url} に募集を投稿しました。",
            ephemeral=True,
        )


class NowPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="男性宛", emoji="💎", style=discord.ButtonStyle.primary, custom_id="noir:now:male")
    async def male(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(NowRecruitModal("male"))

    @discord.ui.button(label="女性宛", emoji="💗", style=discord.ButtonStyle.danger, custom_id="noir:now:female")
    async def female(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(NowRecruitModal("female"))

    @discord.ui.button(label="全員宛", emoji="🐣", style=discord.ButtonStyle.success, custom_id="noir:now:all")
    async def all_members(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(NowRecruitModal("all"))


# =========================================================
# 裏募集
# =========================================================

class FilterRecruitModal(discord.ui.Modal):
    def __init__(self, mode: str):
        super().__init__(title="裏募集を作成")
        self.mode = mode

        self.recruit_title = discord.ui.TextInput(
            label="募集タイトル",
            placeholder="例：今夜ゆっくり話せる人",
            max_length=100,
        )
        self.body = discord.ui.TextInput(
            label="募集内容",
            placeholder="募集内容を入力してください",
            max_length=1000,
            style=discord.TextStyle.paragraph,
        )
        self.conditions = discord.ui.TextInput(
            label="希望条件",
            placeholder="例：25歳以上／落ち着いた声／同性OK",
            max_length=500,
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.mylist = discord.ui.TextInput(
            label="マイリスト限定",
            placeholder="限定する場合は「はい」、しない場合は空欄",
            max_length=10,
            required=False,
        )
        self.add_item(self.recruit_title)
        self.add_item(self.body)
        self.add_item(self.conditions)
        self.add_item(self.mylist)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        owner = interaction.user
        if guild is None or not isinstance(owner, discord.Member):
            await interaction.response.send_message("サーバー内で使用してください。", ephemeral=True)
            return

        channel = get_text_channel(guild, RECRUIT_NOTIFICATION_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message("募集通知チャンネルが見つかりません。", ephemeral=True)
            return

        role_id = ANONYMOUS_NOTIFY_ROLE_ID if self.mode == "anonymous" else NAMED_NOTIFY_ROLE_ID
        role = guild.get_role(role_id)
        mylist_only = str(self.mylist.value).strip() in {"はい", "yes", "YES", "1"}

        embed = discord.Embed(
            title=f"{'匿名' if self.mode == 'anonymous' else '記名'}募集｜{self.recruit_title.value}",
            description=str(self.body.value),
            color=discord.Color.dark_purple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="希望条件",
            value=str(self.conditions.value).strip() or "特になし",
            inline=False,
        )
        embed.add_field(
            name="公開範囲",
            value="マイリスト限定" if mylist_only else "募集対象者全体",
            inline=False,
        )

        if self.mode == "named":
            embed.add_field(name="募集主", value=owner.mention, inline=False)
            embed.set_thumbnail(url=owner.display_avatar.url)
        else:
            embed.set_footer(text="募集主は応募承認後に表示されます")

        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await channel.send(
            content=role.mention if role else None,
            embed=embed,
            view=RecruitActionView(),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        save_recruitment(
            message.id,
            owner.id,
            self.mode,
            str(self.recruit_title.value),
            str(self.body.value),
            str(self.conditions.value),
            mylist_only,
        )
        await send_log(guild, f"📝 裏募集作成：{owner.mention} / message={message.id} / mode={self.mode}")
        await interaction.followup.send(
            f"✅ {message.jump_url} に裏募集を投稿しました。",
            ephemeral=True,
        )


class FilterRecruitPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="匿名で募集", emoji="🎭", style=discord.ButtonStyle.secondary, custom_id="noir:filter:anonymous")
    async def anonymous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(FilterRecruitModal("anonymous"))

    @discord.ui.button(label="記名で募集", emoji="🪪", style=discord.ButtonStyle.primary, custom_id="noir:filter:named")
    async def named(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(FilterRecruitModal("named"))


# =========================================================
# 募集応募・取消
# =========================================================

class ApplicationDecisionView(discord.ui.View):
    def __init__(self, application_id: int):
        super().__init__(timeout=None)
        self.application_id = application_id

        approve = discord.ui.Button(
            label="承認",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"noir:application:approve:{application_id}",
        )
        reject = discord.ui.Button(
            label="お断り",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"noir:application:reject:{application_id}",
        )
        approve.callback = self.approve
        reject.callback = self.reject
        self.add_item(approve)
        self.add_item(reject)

    def _guild(self) -> Optional[discord.Guild]:
        return bot.get_guild(GUILD_ID)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        row = get_application(self.application_id)
        if row is None:
            await interaction.response.send_message(
                "応募情報が見つかりません。",
                ephemeral=True,
            )
            return False

        if row["status"] != "pending":
            await interaction.response.send_message(
                "この応募はすでに処理済みです。",
                ephemeral=True,
            )
            return False

        if interaction.user.id != int(row["owner_id"]):
            await interaction.response.send_message(
                "募集主だけ操作できます。",
                ephemeral=True,
            )
            return False
        return True

    async def approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        row = get_application(self.application_id)
        if row is None or row["status"] != "pending":
            await interaction.followup.send(
                "この応募はすでに処理済みです。",
                ephemeral=True,
            )
            return

        guild = self._guild()
        if guild is None:
            await interaction.followup.send(
                "サーバー情報を取得できませんでした。",
                ephemeral=True,
            )
            return

        owner = guild.get_member(int(row["owner_id"]))
        applicant = guild.get_member(int(row["applicant_id"]))
        if owner is None or applicant is None:
            await interaction.followup.send(
                "募集主または応募者がサーバーに見つかりません。",
                ephemeral=True,
            )
            return

        update_application(self.application_id, "approved")

        parent = get_text_channel(guild, RECRUIT_NOTIFICATION_CHANNEL_ID)
        thread_text = ""
        if parent:
            try:
                thread = await parent.create_thread(
                    name=clean_channel_name(
                        f"連絡用｜{owner.display_name}×{applicant.display_name}"
                    ),
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=1440,
                    reason="募集応募が承認されたため",
                )
                await thread.add_user(owner)
                await thread.add_user(applicant)
                await thread.send(
                    f"{owner.mention} {applicant.mention}\n"
                    "✅ 応募が承認されました。このスレッドで連絡してください。"
                )
                thread_text = f"\n連絡用スレッド：{thread.mention}"
            except Exception:
                log.exception("連絡用スレッド作成失敗")

        try:
            await applicant.send(
                f"✅ **{guild.name}** の募集への立候補が承認されました。"
                f"{thread_text}"
            )
        except discord.HTTPException:
            pass

        await send_log(
            guild,
            f"✅ 応募承認：募集主 {owner.mention} / 応募者 {applicant.mention}",
        )

        if interaction.message:
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else discord.Embed(title="💞 募集への立候補")
                )
                embed.color = discord.Color.green()
                embed.add_field(
                    name="結果",
                    value=f"✅ 承認済み\n応募者：{applicant.mention}{thread_text}",
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            "✅ 立候補を承認しました。応募者にもDMで通知しました。",
            ephemeral=True,
        )

    async def reject(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        row = get_application(self.application_id)
        if row is None or row["status"] != "pending":
            await interaction.followup.send(
                "この応募はすでに処理済みです。",
                ephemeral=True,
            )
            return

        guild = self._guild()
        update_application(self.application_id, "rejected")

        applicant = (
            guild.get_member(int(row["applicant_id"]))
            if guild is not None
            else None
        )
        if applicant and guild:
            try:
                await applicant.send(
                    f"❌ **{guild.name}** の募集への立候補は今回は見送られました。"
                )
            except discord.HTTPException:
                pass

        if guild:
            await send_log(
                guild,
                f"❌ 応募却下：募集主 <@{row['owner_id']}> / "
                f"応募者 <@{row['applicant_id']}>",
            )

        if interaction.message:
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else discord.Embed(title="💞 募集への立候補")
                )
                embed.color = discord.Color.red()
                embed.add_field(
                    name="結果",
                    value="❌ お断り済み",
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            "❌ 立候補をお断りしました。応募者にもDMで通知しました。",
            ephemeral=True,
        )


class RecruitActionView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="立候補", emoji="💞", style=discord.ButtonStyle.success, custom_id="noir:recruit:apply")
    async def apply(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.message is None or interaction.guild is None:
            return

        now_row = get_now_recruitment(interaction.message.id)
        recruit_row = get_recruitment(interaction.message.id)
        row = now_row or recruit_row

        if row is None or not bool(row["active"]):
            await interaction.response.send_message("この募集は終了しています。", ephemeral=True)
            return

        owner_id = int(row["owner_id"])
        if interaction.user.id == owner_id:
            await interaction.response.send_message("自分の募集には立候補できません。", ephemeral=True)
            return

        if is_blocked_either_way(owner_id, interaction.user.id):
            await interaction.response.send_message(
                "ブラックリスト関係のため応募できません。",
                ephemeral=True,
            )
            return

        if recruit_row is not None and bool(recruit_row["mylist_only"]):
            if interaction.user.id not in get_pairs("favorites", owner_id):
                await interaction.response.send_message(
                    "この募集は募集主のマイリスト限定です。",
                    ephemeral=True,
                )
                return

        application_id = create_application(
            interaction.message.id,
            owner_id,
            interaction.user.id,
        )
        owner = interaction.guild.get_member(owner_id)
        confirm = get_text_channel(interaction.guild, RECRUIT_CONFIRM_CHANNEL_ID)

        embed = discord.Embed(
            title="💞 募集への立候補",
            description=f"{interaction.user.mention} さんから立候補が届きました。",
            color=discord.Color.pink(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="元の募集", value=interaction.message.jump_url, inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        if confirm:
            await confirm.send(
                content=owner.mention if owner else None,
                embed=embed,
                view=ApplicationDecisionView(application_id),
                allowed_mentions=discord.AllowedMentions(users=True),
            )

        if owner:
            try:
                dm_embed = discord.Embed(
                    title="💞 募集への立候補",
                    description=(
                        f"**応募者**：{interaction.user.mention}\n"
                        f"**応募者名**：{interaction.user}\n\n"
                        "下のボタンから承認またはお断りを選択できます。"
                    ),
                    color=discord.Color.pink(),
                    timestamp=datetime.now(timezone.utc),
                )
                dm_embed.add_field(
                    name="元の募集",
                    value=interaction.message.jump_url,
                    inline=False,
                )
                dm_embed.set_thumbnail(url=interaction.user.display_avatar.url)
                await owner.send(
                    embed=dm_embed,
                    view=ApplicationDecisionView(application_id),
                )
            except discord.HTTPException:
                pass

        await send_log(
            interaction.guild,
            f"💞 立候補：募集主 <@{owner_id}> / 応募者 {interaction.user.mention}",
        )
        await interaction.response.send_message(
            "✅ 立候補を送信しました。募集主の確認をお待ちください。",
            ephemeral=True,
        )

    @discord.ui.button(label="取消", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="noir:recruit:cancel")
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.message is None:
            return

        now_row = get_now_recruitment(interaction.message.id)
        recruit_row = get_recruitment(interaction.message.id)
        row = now_row or recruit_row

        if row is None:
            await interaction.response.send_message("募集情報が見つかりません。", ephemeral=True)
            return

        member = interaction.user
        allowed = interaction.user.id == int(row["owner_id"]) or (
            isinstance(member, discord.Member) and is_bot_admin(member)
        )
        if not allowed:
            await interaction.response.send_message("募集主または管理者だけ取消できます。", ephemeral=True)
            return

        if now_row:
            close_now_recruitment(interaction.message.id)
        else:
            close_recruitment(interaction.message.id)

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.dark_grey()
        embed.set_footer(text="この募集は終了しました")
        await interaction.response.edit_message(embed=embed, view=None)
        if interaction.guild:
            await send_log(interaction.guild, f"🗑️ 募集取消：message={interaction.message.id}")




# =========================================================
# プロフィール審査
# =========================================================

PROFILE_TEMPLATE_FIELDS = (
    "【名前】",
    "【性別/年齢】",
    "【住まい】",
    "【主な出没時間】",
    "【個室のプレイスタイル】",
    "【通話の可否】",
    "【フェチ・好きな属性】",
    "【嫌いなタイプ・NG行為】",
    "【自分の取扱説明書】",
    "【最後に】",
)


def missing_profile_fields(profile_text: str) -> list[str]:
    return [field for field in PROFILE_TEMPLATE_FIELDS if field not in profile_text]


class ProfileReviewView(discord.ui.View):
    def __init__(self, review_id: int):
        super().__init__(timeout=None)
        self.review_id = review_id

        approve = discord.ui.Button(
            label="合格",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"noir:profile_review:approve:{review_id}",
        )
        reject = discord.ui.Button(
            label="不合格",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"noir:profile_review:reject:{review_id}",
        )
        approve.callback = self.approve
        reject.callback = self.reject
        self.add_item(approve)
        self.add_item(reject)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        row = get_profile_review(self.review_id)
        if row is None:
            await interaction.response.send_message(
                "審査データが見つかりません。",
                ephemeral=True,
            )
            return False

        if row["status"] != "pending":
            await interaction.response.send_message(
                "このプロフィールはすでに審査済みです。",
                ephemeral=True,
            )
            return False

        member = interaction.user
        allowed = (
            isinstance(member, discord.Member)
            and (
                member.guild_permissions.administrator
                or member.get_role(PROFILE_REVIEW_ROLE_ID) is not None
            )
        )
        if not allowed:
            await interaction.response.send_message(
                "審査ロールを持っている人だけ操作できます。",
                ephemeral=True,
            )
            return False
        return True

    async def approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        row = get_profile_review(self.review_id)
        if row is None or row["status"] != "pending":
            await interaction.followup.send(
                "このプロフィールはすでに審査済みです。",
                ephemeral=True,
            )
            return

        guild = bot.get_guild(int(row["guild_id"]))
        if guild is None:
            await interaction.followup.send(
                "サーバー情報を取得できません。",
                ephemeral=True,
            )
            return

        member = guild.get_member(int(row["member_id"]))
        if member is None:
            update_profile_review(self.review_id, "member_missing")
            await interaction.followup.send(
                "投稿者がサーバーに見つかりません。",
                ephemeral=True,
            )
            return

        gender = str(row["gender"])
        if gender not in {"male", "female"}:
            await interaction.followup.send(
                "【性別/年齢】から男性・女性を判定できません。\n"
                "投稿者にプロフィールを修正してもらってください。",
                ephemeral=True,
            )
            return

        verified_role = guild.get_role(VERIFIED_ROLE_ID)
        male_role = guild.get_role(MALE_ROLE_ID)
        female_role = guild.get_role(FEMALE_ROLE_ID)

        target_role = male_role if gender == "male" else female_role
        opposite_role = female_role if gender == "male" else male_role

        if verified_role is None or target_role is None:
            await interaction.followup.send(
                "確認ロールまたは性別ロールが見つかりません。",
                ephemeral=True,
            )
            return

        try:
            roles_to_add = [verified_role, target_role]
            await member.add_roles(
                *roles_to_add,
                reason=f"プロフィール審査合格 / reviewer={interaction.user}",
            )
            if opposite_role and opposite_role in member.roles:
                await member.remove_roles(
                    opposite_role,
                    reason="プロフィール審査で性別ロールを整理",
                )
        except discord.Forbidden:
            await interaction.followup.send(
                "ロールを付与できませんでした。\n"
                "NOIRbotのロールを、確認・男性・女性ロールより上に置いてください。",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            log.exception("プロフィール合格ロール付与失敗")
            await interaction.followup.send(
                "ロール付与中にDiscordエラーが発生しました。",
                ephemeral=True,
            )
            return

        destination_id = (
            MALE_PROFILE_CHANNEL_ID
            if gender == "male"
            else FEMALE_PROFILE_CHANNEL_ID
        )
        destination = get_text_channel(guild, destination_id)

        profile_jump = ""
        if destination:
            try:
                profile_embed = discord.Embed(
                    title=f"🌙 {member.display_name}｜PROFILE",
                    description=str(row["profile_text"])[:4096],
                    color=discord.Color.blurple(),
                    timestamp=datetime.now(timezone.utc),
                )
                profile_embed.set_author(
                    name=str(member),
                    icon_url=member.display_avatar.url,
                )
                profile_embed.set_footer(text=f"User ID: {member.id}")
                posted = await destination.send(
                    content=member.mention,
                    embed=profile_embed,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
                profile_jump = posted.jump_url
            except discord.HTTPException:
                log.exception("正式プロフィール投稿失敗")
        else:
            log.error("正式プロフィールチャンネルが見つかりません: %s", destination_id)

        update_profile_review(self.review_id, "approved")

        try:
            dm_text = (
                f"✅ **{guild.name}** のプロフィール審査に合格しました！\n"
                f"確認ロールと{'男性' if gender == 'male' else '女性'}ロールを付与しました。"
            )
            if profile_jump:
                dm_text += f"\n\n正式プロフィール：{profile_jump}"
            await member.send(dm_text)
        except discord.HTTPException:
            pass

        if interaction.message:
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else discord.Embed(title="📋 プロフィール審査")
                )
                embed.color = discord.Color.green()
                embed.add_field(
                    name="審査結果",
                    value=(
                        f"✅ 合格\n"
                        f"審査担当：{interaction.user.mention}\n"
                        f"付与：{verified_role.mention} / {target_role.mention}"
                    ),
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            "✅ 合格処理が完了しました。本人にもDMを送りました。",
            ephemeral=True,
        )

    async def reject(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        row = get_profile_review(self.review_id)
        if row is None or row["status"] != "pending":
            await interaction.followup.send(
                "このプロフィールはすでに審査済みです。",
                ephemeral=True,
            )
            return

        guild = bot.get_guild(int(row["guild_id"]))
        member = (
            guild.get_member(int(row["member_id"]))
            if guild is not None
            else None
        )

        update_profile_review(self.review_id, "rejected")

        if member and guild:
            try:
                await member.send(
                    f"❌ **{guild.name}** のプロフィール審査は今回は不合格となりました。\n"
                    "内容を確認・修正して、必要であれば再度プロフィールを投稿してください。"
                )
            except discord.HTTPException:
                pass

        if interaction.message:
            try:
                embed = (
                    interaction.message.embeds[0]
                    if interaction.message.embeds
                    else discord.Embed(title="📋 プロフィール審査")
                )
                embed.color = discord.Color.red()
                embed.add_field(
                    name="審査結果",
                    value=f"❌ 不合格\n審査担当：{interaction.user.mention}",
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            "❌ 不合格処理が完了しました。本人にもDMを送りました。",
            ephemeral=True,
        )


async def handle_temp_profile_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return
    if message.channel.id != TEMP_PROFILE_CHANNEL_ID:
        return
    if not isinstance(message.author, discord.Member):
        return

    profile_text = message.content.strip()
    if not profile_text:
        return

    missing = missing_profile_fields(profile_text)
    gender = detect_profile_gender(profile_text)

    review_id = create_profile_review(
        message.guild.id,
        message.id,
        message.author.id,
        profile_text,
        gender,
    )

    review_role = message.guild.get_role(PROFILE_REVIEW_ROLE_ID)
    gender_text = (
        "男性" if gender == "male"
        else "女性" if gender == "female"
        else "⚠️ 判定できません"
    )

    embed = discord.Embed(
        title="📋 仮プロフィール審査",
        description=profile_text[:4096],
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="投稿者",
        value=f"{message.author.mention} (`{message.author.id}`)",
        inline=False,
    )
    embed.add_field(
        name="性別判定",
        value=gender_text,
        inline=True,
    )
    embed.add_field(
        name="テンプレ確認",
        value=(
            "✅ 全項目あり"
            if not missing
            else "⚠️ 不足：" + "、".join(missing)
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="元の投稿",
        value=message.jump_url,
        inline=False,
    )
    embed.set_thumbnail(url=message.author.display_avatar.url)

    review_channel = get_text_channel(message.guild, PROFILE_REVIEW_CHANNEL_ID)
    if review_channel is None:
        log.error(
            "プロフィール審査管理チャンネルが見つかりません: %s",
            PROFILE_REVIEW_CHANNEL_ID,
        )
        try:
            await message.author.send(
                "⚠️ プロフィールは受け付けましたが、審査管理チャンネルが見つからないため"
                "管理者へ送信できませんでした。管理者へご連絡ください。"
            )
        except discord.HTTPException:
            pass
        return

    try:
        review_message = await review_channel.send(
            content=review_role.mention if review_role else None,
            embed=embed,
            view=ProfileReviewView(review_id),
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )
        set_profile_review_message(review_id, review_message.id)

        try:
            await message.author.send(
                f"📋 **{message.guild.name}** の仮プロフィールを受け付けました。\n"
                "現在、管理者による審査待ちです。結果はBotからDMでお知らせします。"
            )
        except discord.HTTPException:
            pass
    except discord.HTTPException:
        log.exception("プロフィール審査管理チャンネルへの投稿失敗")


# =========================================================
# DM申請
# =========================================================

def member_has_role(member: discord.Member, role_id: int) -> bool:
    if not role_id:
        return False
    return any(role.id == role_id for role in member.roles)


async def send_dm_log(guild: discord.Guild, text: str) -> None:
    settings = get_dm_settings(guild.id)
    if not settings:
        return
    log_channel_id = int(settings["log_channel_id"])
    if not log_channel_id:
        return
    channel = get_text_channel(guild, log_channel_id)
    if channel is None:
        return
    try:
        await channel.send(text)
    except discord.HTTPException:
        log.exception("DM申請ログ送信失敗")


class DMDecisionView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.request_id = request_id

        approve = discord.ui.Button(
            label="承認する",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"noir:dm_request:approve:{request_id}",
        )
        reject = discord.ui.Button(
            label="お断り",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"noir:dm_request:reject:{request_id}",
        )
        approve.callback = self.approve
        reject.callback = self.reject
        self.add_item(approve)
        self.add_item(reject)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        row = get_dm_request(self.request_id)
        if row is None:
            await interaction.response.send_message(
                "このDM申請データは見つかりません。",
                ephemeral=True,
            )
            return False

        if row["status"] != "pending":
            await interaction.response.send_message(
                "このDM申請はすでに処理済みです。",
                ephemeral=True,
            )
            return False

        member = interaction.user
        is_admin = (
            isinstance(member, discord.Member)
            and member.guild_permissions.administrator
        )
        if interaction.user.id != int(row["target_id"]) and not is_admin:
            await interaction.response.send_message(
                "申請された本人または管理者だけ操作できます。",
                ephemeral=True,
            )
            return False
        return True

    async def approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        row = get_dm_request(self.request_id)
        if row is None or row["status"] != "pending":
            await interaction.followup.send(
                "この申請はすでに処理済みです。",
                ephemeral=True,
            )
            return

        set_dm_request_status(self.request_id, "approved")

        guild = interaction.guild
        requester = guild.get_member(int(row["requester_id"])) if guild else None
        target = guild.get_member(int(row["target_id"])) if guild else None

        embed = discord.Embed(
            title="✅ DM申請が承認されました",
            description=(
                f"{requester.mention if requester else f'<@{row['requester_id']}>'} さんの"
                "DM申請が承認されました。\n\n"
                "これ以降のDMは、お互いのルールと合意を守って利用してください。"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )

        if interaction.message:
            try:
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        if requester:
            try:
                await requester.send(
                    f"✅ **{guild.name}** で {target.mention if target else '相手'} さんへの"
                    "DM申請が承認されました。"
                )
            except discord.HTTPException:
                pass

        if guild:
            await send_dm_log(
                guild,
                f"✅ DM申請承認｜申請者 <@{row['requester_id']}> → 相手 <@{row['target_id']}> "
                f"｜request={self.request_id}",
            )

        await interaction.followup.send(
            "✅ DM申請を承認しました。",
            ephemeral=True,
        )

    async def reject(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        row = get_dm_request(self.request_id)
        if row is None or row["status"] != "pending":
            await interaction.followup.send(
                "この申請はすでに処理済みです。",
                ephemeral=True,
            )
            return

        set_dm_request_status(self.request_id, "rejected")

        guild = interaction.guild
        requester = guild.get_member(int(row["requester_id"])) if guild else None

        embed = discord.Embed(
            title="❌ DM申請はお断りされました",
            description="今回はDM申請が承認されませんでした。",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )

        if interaction.message:
            try:
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        if requester:
            try:
                await requester.send(
                    f"❌ **{guild.name}** でのDM申請は今回はお断りとなりました。"
                )
            except discord.HTTPException:
                pass

        if guild:
            await send_dm_log(
                guild,
                f"❌ DM申請拒否｜申請者 <@{row['requester_id']}> → 相手 <@{row['target_id']}> "
                f"｜request={self.request_id}",
            )

        await interaction.followup.send(
            "❌ DM申請をお断りしました。",
            ephemeral=True,
        )

        thread = interaction.channel
        if isinstance(thread, discord.Thread):
            try:
                await thread.edit(archived=True, locked=True)
            except discord.HTTPException:
                pass


class DMTargetSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="DM申請する相手を選択してください",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        requester = interaction.user
        if guild is None or not isinstance(requester, discord.Member):
            await interaction.followup.send(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        selected = self.values[0]
        target = guild.get_member(selected.id)
        if target is None:
            await interaction.followup.send(
                "相手のメンバー情報を取得できませんでした。",
                ephemeral=True,
            )
            return

        if target.bot or target.id == requester.id:
            await interaction.followup.send(
                "自分自身またはBotには申請できません。",
                ephemeral=True,
            )
            return

        settings = get_dm_settings(guild.id)
        if settings is None:
            await interaction.followup.send(
                "DM申請パネルの設定がありません。管理者に確認してください。",
                ephemeral=True,
            )
            return

        ng_role_id = int(settings["dm_ng_role_id"])
        free_role_id = int(settings["dm_free_role_id"])

        if member_has_role(target, ng_role_id):
            await interaction.followup.send(
                f"🚫 {target.mention} さんはDM申請を受け付けていません。",
                ephemeral=True,
            )
            return

        if member_has_role(target, free_role_id):
            await interaction.followup.send(
                f"⭕ {target.mention} さんは無断DM許可ロールを持っているため、"
                "DM申請は不要です。",
                ephemeral=True,
            )
            return

        if is_blocked_either_way(requester.id, target.id):
            await interaction.followup.send(
                "ブラックリスト関係の相手にはDM申請できません。",
                ephemeral=True,
            )
            return

        if pending_dm_request_between(guild.id, requester.id, target.id):
            await interaction.followup.send(
                "この相手への未処理DM申請がすでにあります。",
                ephemeral=True,
            )
            return

        panel_channel = guild.get_channel(int(settings["panel_channel_id"]))
        if not isinstance(panel_channel, discord.TextChannel):
            await interaction.followup.send(
                "DM申請チャンネルが見つかりません。管理者に確認してください。",
                ephemeral=True,
            )
            return

        request_id = create_dm_request_record(
            guild.id,
            requester.id,
            target.id,
        )

        thread_name = (
            f"dm申請-{requester.display_name}-{target.display_name}"
        )[:95]

        try:
            thread = await panel_channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=1440,
                reason=f"DM申請: {requester} -> {target}",
            )
            await thread.add_user(requester)
            await thread.add_user(target)

            embed = discord.Embed(
                title="🍜 DM申請",
                description=(
                    f"**申請者**：{requester.mention}\n"
                    f"**申請先**：{target.mention}\n\n"
                    "このスレッドで必要な確認を行ってください。\n"
                    "申請先の方は、下のボタンから承認またはお断りを選択してください。"
                ),
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            decision = await thread.send(
                content=target.mention,
                embed=embed,
                view=DMDecisionView(request_id),
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
            update_dm_request_location(
                request_id,
                thread.id,
                decision.id,
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("DM申請スレッド作成失敗")
            set_dm_request_status(request_id, "failed")
            await interaction.followup.send(
                "DM申請スレッドを作成できませんでした。\n"
                "Botに「プライベートスレッドを作成」「スレッドを管理」権限があるか確認してください。",
                ephemeral=True,
            )
            return

        await send_dm_log(
            guild,
            f"🍜 DM申請作成｜{requester.mention} → {target.mention}｜{thread.mention}",
        )

        await interaction.followup.send(
            f"✅ {target.mention} さんとのDM申請スレッドを作成しました。\n"
            f"{thread.mention}",
            ephemeral=True,
        )


class DMTargetSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(DMTargetSelect())


class DMRequestPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="申請する",
        emoji="🍜",
        style=discord.ButtonStyle.primary,
        custom_id="noir:dm_request:start",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "DM申請する相手を選択してください。",
            ephemeral=True,
            view=DMTargetSelectView(),
        )


# =========================================================
# Bot
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.message_content = True


class NoirBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.timer_tasks: dict[int, asyncio.Task] = {}
        self.empty_tasks: dict[int, asyncio.Task] = {}

    async def setup_hook(self) -> None:
        init_db()

        self.add_view(QuickPanel())
        self.add_view(PrivatePanel())
        self.add_view(PublicSpecialRoomPanel())
        self.add_view(DMRequestPanel())
        self.add_view(VCMenuView())

        # 再起動後も未処理プロフィール審査ボタンを復元
        for profile_row in all_pending_profile_reviews():
            review_message_id = int(profile_row["review_message_id"])
            if review_message_id:
                self.add_view(
                    ProfileReviewView(int(profile_row["id"])),
                    message_id=review_message_id,
                )

        # 再起動後も未処理DM申請の承認/拒否ボタンを復元
        for dm_row in all_pending_dm_requests():
            if int(dm_row["decision_message_id"]):
                self.add_view(
                    DMDecisionView(int(dm_row["id"])),
                    message_id=int(dm_row["decision_message_id"]),
                )

        # 再起動後も募集立候補のDM承認/拒否ボタンを復元
        for app_row in all_pending_applications():
            self.add_view(
                ApplicationDecisionView(int(app_row["id"]))
            )

        # 再起動後もノックDMの承認/拒否ボタンを復元
        for knock_row in all_pending_knock_requests():
            self.add_view(
                KnockDMDecisionView(int(knock_row["id"]))
            )

        # 再起動後も既存の外部ノックパネルを使えるようにする
        for row in all_rooms():
            knock_message_id = (
                int(row["knock_message_id"])
                if "knock_message_id" in row.keys()
                else 0
            )
            if knock_message_id:
                self.add_view(
                    ExternalKnockPanelView(int(row["channel_id"])),
                    message_id=knock_message_id,
                )

        self.add_view(NowPanel())
        self.add_view(FilterRecruitPanel())
        self.add_view(RecruitActionView())

        guild_obj = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild_obj)

        synced = await self.tree.sync(guild=guild_obj)
        log.info("Synced %s guild commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in: %s (%s)", self.user, self.user.id if self.user else "?")
        guild = self.get_guild(GUILD_ID)
        if guild:
            log.info("Guild: %s (%s)", guild.name, guild.id)
        else:
            log.error("Guild %s が見つかりません", GUILD_ID)

        for row in all_rooms():
            guild = self.get_guild(int(row["guild_id"]))
            channel = guild.get_channel(int(row["channel_id"])) if guild else None
            if not isinstance(channel, discord.VoiceChannel):
                delete_room_record(int(row["channel_id"]))
                continue

            owner = guild.get_member(int(row["owner_id"])) if guild else None
            if owner:
                try:
                    await apply_quick_role_visibility(
                        channel,
                        owner,
                        str(row["room_type"]),
                    )
                except discord.Forbidden:
                    log.warning("既存クイック部屋の権限補正に失敗: %s", channel.id)
                except discord.HTTPException:
                    log.exception("既存クイック部屋の権限補正エラー: %s", channel.id)

    async def start_room_timer(self, channel: discord.VoiceChannel) -> None:
        if channel.id in self.timer_tasks:
            return

        set_timer_started(channel.id, True)

        async def runner() -> None:
            try:
                await asyncio.sleep(TIMED_ROOM_SECONDS)
                current = channel.guild.get_channel(channel.id)
                if isinstance(current, discord.VoiceChannel):
                    row = get_room(current.id)
                    if row:
                        await disable_external_knock_panel(
                            self,
                            current.guild,
                            row,
                            reason_text="時間制限により部屋が終了しました",
                        )
                    await current.delete(reason="時間制部屋の制限時間終了")
            except asyncio.CancelledError:
                raise
            except discord.NotFound:
                pass
            except Exception:
                log.exception("時間制部屋削除失敗")
            finally:
                delete_room_record(channel.id)
                self.timer_tasks.pop(channel.id, None)

        self.timer_tasks[channel.id] = asyncio.create_task(runner())

    async def schedule_empty_delete(self, channel: discord.VoiceChannel) -> None:
        old = self.empty_tasks.pop(channel.id, None)
        if old:
            old.cancel()

        async def runner() -> None:
            try:
                await asyncio.sleep(EMPTY_DELETE_SECONDS)
                current = channel.guild.get_channel(channel.id)
                if isinstance(current, discord.VoiceChannel) and not current.members:
                    row = get_room(current.id)
                    if row:
                        await disable_external_knock_panel(
                            self,
                            current.guild,
                            row,
                            reason_text="空室のため部屋が終了しました",
                        )
                    await current.delete(reason="空室になったため削除")
                    delete_room_record(channel.id)
            except asyncio.CancelledError:
                raise
            except discord.NotFound:
                delete_room_record(channel.id)
            except Exception:
                log.exception("空室削除失敗")
            finally:
                self.empty_tasks.pop(channel.id, None)

        self.empty_tasks[channel.id] = asyncio.create_task(runner())

    async def hide_room(self, channel: discord.VoiceChannel, owner_id: int) -> None:
        await channel.set_permissions(
            channel.guild.default_role,
            view_channel=False,
            connect=False,
            reason="2人揃ったため非表示",
        )
        owner = channel.guild.get_member(owner_id)
        allowed = list(channel.members)
        if owner and owner not in allowed:
            allowed.append(owner)
        for member in allowed:
            await channel.set_permissions(
                member,
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
            )

    async def on_message(self, message: discord.Message) -> None:
        await handle_temp_profile_message(message)
        await self.process_commands(message)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        ids = {
            ch.id
            for ch in (before.channel, after.channel)
            if isinstance(ch, discord.VoiceChannel)
        }

        for channel_id in ids:
            row = get_room(channel_id)
            if row is None:
                continue

            channel = member.guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                delete_room_record(channel_id)
                continue

            real_members = [m for m in channel.members if not m.bot]

            if real_members:
                task = self.empty_tasks.pop(channel.id, None)
                if task:
                    task.cancel()
            else:
                await self.schedule_empty_delete(channel)
                continue

            if bool(row["hidden_when_two"]) and len(real_members) >= 2:
                try:
                    await self.hide_room(channel, int(row["owner_id"]))
                except Exception:
                    log.exception("部屋非表示化失敗")

            if bool(row["timed"]) and len(real_members) >= 2:
                await self.start_room_timer(channel)


bot = NoirBot()


# =========================================================
# スラッシュコマンド
# =========================================================

def admin_only(interaction: discord.Interaction) -> bool:
    return isinstance(interaction.user, discord.Member) and is_bot_admin(interaction.user)


@bot.tree.command(name="setup_quick_panel", description="クイック作成パネルを設置")
@app_commands.guild_only()
async def setup_quick_panel(interaction: discord.Interaction) -> None:
    if not admin_only(interaction):
        await interaction.response.send_message("管理者専用です。", ephemeral=True)
        return

    embed = discord.Embed(
        title="➕ クイック作成",
        description=(
            "作成したい部屋のボタンを押してください。\n\n"
            "🌱 表マイリスト部屋\n"
            "📚 裏マイリスト部屋\n"
            "🌱 表QM部屋\n"
            "⏰ 表／裏時間制部屋\n"
            "💤 裏添い寝部屋\n"
            "🔞 エロイプ部屋\n"
            "🌙 裏QM部屋"
        ),
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=QuickPanel())
    await interaction.response.send_message("✅ 設置しました。", ephemeral=True)


@bot.tree.command(name="setup_private_panel", description="個室作成パネルを設置")
@app_commands.guild_only()
async def setup_private_panel(interaction: discord.Interaction) -> None:
    if not admin_only(interaction):
        await interaction.response.send_message("管理者専用です。", ephemeral=True)
        return

    embed = discord.Embed(
        title="➕ 個室作成",
        description=(
            "⭕ **表個室**\n全員から見えますが、選択した人だけ接続できます。\n\n"
            "🚫 **裏個室**\n作成者と選択した人だけ閲覧・接続できます。"
        ),
        color=discord.Color.green(),
    )
    await interaction.channel.send(embed=embed, view=PrivatePanel())
    await interaction.response.send_message("✅ 設置しました。", ephemeral=True)


@bot.tree.command(name="setup_now_panel", description="なう募集パネルを設置")
@app_commands.guild_only()
async def setup_now_panel(interaction: discord.Interaction) -> None:
    if not admin_only(interaction):
        await interaction.response.send_message("管理者専用です。", ephemeral=True)
        return

    embed = discord.Embed(
        title="🐣 なう募集",
        description=(
            "男性／女性／全員に\n"
            "「誰か話そう」という募集をかけることができます。\n\n"
            "💎 **男性宛**\n"
            "💗 **女性宛**\n"
            "🐣 **全員宛**"
        ),
        color=discord.Color.from_rgb(255, 190, 70),
    )
    await interaction.channel.send(embed=embed, view=NowPanel())
    await interaction.response.send_message("✅ なう募集パネルを設置しました。", ephemeral=True)


@bot.tree.command(name="setup_recruitment_panels", description="裏募集作成パネルを設置")
@app_commands.guild_only()
async def setup_recruitment_panels(interaction: discord.Interaction) -> None:
    if not admin_only(interaction):
        await interaction.response.send_message("管理者専用です。", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        return

    channel = get_text_channel(guild, RECRUIT_CREATE_PANEL_CHANNEL_ID) or interaction.channel
    embed = discord.Embed(
        title="🌙 フィルタリング付き裏募集",
        description=(
            "募集方法を選択してください。\n\n"
            "🎭 **匿名で募集**\n"
            "募集主を伏せた状態で投稿します。\n\n"
            "🪪 **記名で募集**\n"
            "募集主を表示して投稿します。\n\n"
            "ブラックリスト関係の相手は応募できません。"
        ),
        color=discord.Color.dark_purple(),
    )
    await channel.send(embed=embed, view=FilterRecruitPanel())
    await interaction.response.send_message("✅ 裏募集作成パネルを設置しました。", ephemeral=True)




@bot.tree.command(
    name="setup_public_room_panel",
    description="公開エロイプ・ラジオ部屋の作成パネルを設置します",
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_public_room_panel(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    if interaction.guild is None:
        await interaction.followup.send(
            "サーバー内で使用してください。",
            ephemeral=True,
        )
        return

    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.followup.send(
            "テキストチャンネルを指定してください。",
            ephemeral=True,
        )
        return

    eroip_embed = discord.Embed(
        title="🔞 公開エロイプ部屋作成",
        description=(
            "エロイプ公開をする部屋を作成します。\n"
            "作成者のブラックリスト／マイリストが反映されます。\n\n"
            "🐑 **マイリストで作成**\n"
            "マイリスト登録者だけが接続できます。\n\n"
            "🚫 **ブラックリストで作成**\n"
            "公開部屋として作成し、ブラックリスト登録者は"
            "閲覧・接続できません。"
        ),
        color=discord.Color.purple(),
    )

    radio_embed = discord.Embed(
        title="📻 ラジオ部屋作成",
        description=(
            "歌・ゲーム・読み聞かせなどを配信できる公開部屋です。\n"
            "作成者のブラックリスト／マイリストが反映されます。\n\n"
            "🐑 **マイリストで作成**\n"
            "マイリスト登録者だけが接続できます。\n\n"
            "🚫 **ブラックリストで作成**\n"
            "公開部屋として作成し、ブラックリスト登録者は"
            "閲覧・接続できません。"
        ),
        color=discord.Color.light_grey(),
    )

    try:
        await target.send(
            embeds=[eroip_embed, radio_embed],
            view=PublicSpecialRoomPanel(),
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("公開部屋パネル設置失敗")
        await interaction.followup.send(
            "パネルを設置できませんでした。Botの権限を確認してください。",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"✅ {target.mention} に公開部屋パネルを設置しました。",
        ephemeral=True,
    )




@bot.tree.command(
    name="setup_dm_panel",
    description="DM申請パネルを設置します",
)
@app_commands.describe(
    channel="DM申請パネルを設置するチャンネル",
    dm_ng_role="DM申請を受け付けない人用ロール（任意）",
    dm_free_role="申請なしDMを許可する人用ロール（任意）",
    log_channel="DM申請ログの送信先（任意）",
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_dm_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    dm_ng_role: Optional[discord.Role] = None,
    dm_free_role: Optional[discord.Role] = None,
    log_channel: Optional[discord.TextChannel] = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send(
            "サーバー内で使用してください。",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🍜 DM申請",
        description=(
            "【申請する】を押してユーザーを選択すると、"
            "二人専用のプライベートスレッドが作成されます。\n"
            "そのスレッド内で相手にDM申請を行ってください。\n\n"
            + (
                f"🚫 {dm_ng_role.mention} を持っている人には申請できません。\n"
                if dm_ng_role else
                "🚫 DM申請NGロールは未設定です。\n"
            )
            + (
                f"⭕ {dm_free_role.mention} を持っている人は申請不要です。\n"
                if dm_free_role else
                "⭕ 無断DM許可ロールは未設定です。\n"
            )
            + "\nブラックリスト関係の相手には申請できません。"
        ),
        color=discord.Color.orange(),
    )

    try:
        message = await channel.send(
            embed=embed,
            view=DMRequestPanel(),
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("DM申請パネル設置失敗")
        await interaction.followup.send(
            "パネルを設置できませんでした。Botの権限を確認してください。",
            ephemeral=True,
        )
        return

    save_dm_settings(
        guild.id,
        channel.id,
        dm_ng_role.id if dm_ng_role else 0,
        dm_free_role.id if dm_free_role else 0,
        log_channel.id if log_channel else 0,
        message.id,
    )

    await interaction.followup.send(
        f"✅ {channel.mention} にDM申請パネルを設置しました。",
        ephemeral=True,
    )


@bot.tree.command(name="mylist_add", description="マイリストに追加")
@app_commands.describe(user="追加するユーザー")
@app_commands.guild_only()
async def mylist_add(interaction: discord.Interaction, user: discord.Member) -> None:
    if user.bot or user.id == interaction.user.id:
        await interaction.response.send_message("自分自身またはBotは追加できません。", ephemeral=True)
        return
    if is_blocked_either_way(interaction.user.id, user.id):
        await interaction.response.send_message("ブラックリスト関係の相手は追加できません。", ephemeral=True)
        return

    added = add_pair("favorites", interaction.user.id, user.id)
    await interaction.response.send_message(
        f"⭐ {user.mention} をマイリストに追加しました。"
        if added else "すでに登録されています。",
        ephemeral=True,
    )


@bot.tree.command(name="mylist_remove", description="マイリストから削除")
@app_commands.describe(user="削除するユーザー")
@app_commands.guild_only()
async def mylist_remove(interaction: discord.Interaction, user: discord.Member) -> None:
    removed = remove_pair("favorites", interaction.user.id, user.id)
    await interaction.response.send_message(
        f"🗑️ {user.mention} を削除しました。" if removed else "登録されていません。",
        ephemeral=True,
    )


@bot.tree.command(name="mylist_view", description="マイリストを表示")
@app_commands.guild_only()
async def mylist_view(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    lines = []
    if guild:
        for uid in get_pairs("favorites", interaction.user.id):
            member = guild.get_member(uid)
            lines.append(member.mention if member else f"`{uid}`")
    await interaction.response.send_message(
        "⭐ **マイリスト**\n" + ("\n".join(f"・{x}" for x in lines) if lines else "登録なし"),
        ephemeral=True,
    )


@bot.tree.command(name="blacklist_add", description="ブラックリストに追加")
@app_commands.describe(user="追加するユーザー")
@app_commands.guild_only()
async def blacklist_add(interaction: discord.Interaction, user: discord.Member) -> None:
    if user.bot or user.id == interaction.user.id:
        await interaction.response.send_message("自分自身またはBotは追加できません。", ephemeral=True)
        return

    added = add_pair("blacklist", interaction.user.id, user.id)
    remove_pair("favorites", interaction.user.id, user.id)
    await interaction.response.send_message(
        f"🚫 {user.mention} をブラックリストに追加しました。"
        if added else "すでに登録されています。",
        ephemeral=True,
    )


@bot.tree.command(name="blacklist_remove", description="ブラックリストから削除")
@app_commands.describe(user="解除するユーザー")
@app_commands.guild_only()
async def blacklist_remove(interaction: discord.Interaction, user: discord.Member) -> None:
    removed = remove_pair("blacklist", interaction.user.id, user.id)
    await interaction.response.send_message(
        f"✅ {user.mention} を解除しました。" if removed else "登録されていません。",
        ephemeral=True,
    )


@bot.tree.command(name="blacklist_view", description="ブラックリストを表示")
@app_commands.guild_only()
async def blacklist_view(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    lines = []
    if guild:
        for uid in get_pairs("blacklist", interaction.user.id):
            member = guild.get_member(uid)
            lines.append(member.mention if member else f"`{uid}`")
    await interaction.response.send_message(
        "🚫 **ブラックリスト**\n" + ("\n".join(f"・{x}" for x in lines) if lines else "登録なし"),
        ephemeral=True,
    )


@bot.tree.command(name="room_delete", description="自分の作成部屋を削除")
@app_commands.describe(channel="削除するVC")
@app_commands.guild_only()
async def room_delete(interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
    row = get_room(channel.id)
    member = interaction.user
    if row is None:
        await interaction.response.send_message("Bot作成部屋ではありません。", ephemeral=True)
        return

    if int(row["owner_id"]) != interaction.user.id and not (
        isinstance(member, discord.Member) and is_bot_admin(member)
    ):
        await interaction.response.send_message("作成者または管理者だけ削除できます。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await disable_external_knock_panel(
        interaction.client,
        channel.guild,
        row,
        reason_text="部屋主または管理者がこの部屋を終了しました",
    )
    await channel.delete(reason=f"{interaction.user} が削除")
    delete_room_record(channel.id)
    await interaction.followup.send("✅ 削除しました。", ephemeral=True)


@bot.tree.command(name="room_invite", description="自分の部屋にユーザーを招待")
@app_commands.describe(channel="招待先VC", user="招待するユーザー")
@app_commands.guild_only()
async def room_invite(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    user: discord.Member,
) -> None:
    row = get_room(channel.id)
    if row is None or int(row["owner_id"]) != interaction.user.id:
        await interaction.response.send_message("自分の作成部屋だけ操作できます。", ephemeral=True)
        return
    if is_blocked_either_way(interaction.user.id, user.id):
        await interaction.response.send_message("ブラックリスト関係の相手は招待できません。", ephemeral=True)
        return

    await channel.set_permissions(
        user,
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
        use_voice_activation=True,
    )
    await interaction.response.send_message(f"✅ {user.mention} を招待しました。", ephemeral=True)


@bot.tree.command(name="room_uninvite", description="自分の部屋からユーザーを解除")
@app_commands.describe(channel="対象VC", user="解除するユーザー")
@app_commands.guild_only()
async def room_uninvite(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    user: discord.Member,
) -> None:
    row = get_room(channel.id)
    if row is None or int(row["owner_id"]) != interaction.user.id:
        await interaction.response.send_message("自分の作成部屋だけ操作できます。", ephemeral=True)
        return

    if user.voice and user.voice.channel and user.voice.channel.id == channel.id:
        await user.move_to(None, reason="招待解除")
    await channel.set_permissions(user, overwrite=None)
    await interaction.response.send_message(f"✅ {user.mention} の招待を解除しました。", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN or TOKEN == "ここにBOTトークン":
        raise RuntimeError("環境変数 DISCORD_TOKEN を設定してください。")
    bot.run(TOKEN)
