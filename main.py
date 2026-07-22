from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# 設定：ここだけ変更してください
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "ここにBOTトークン")

GUILD_ID = 1482224471606820874

# 作成先カテゴリ
QUICK_PUBLIC_CATEGORY_ID = 1521453832788246690       # 表マイリスト・表QM・表時間制
QUICK_PRIVATE_CATEGORY_ID = 1516009559892951060      # 裏マイリスト・裏時間制・添い寝・エロイプ・裏QM
PRIVATE_PUBLIC_CATEGORY_ID = 1521453832788246690     # 表個室
PRIVATE_HIDDEN_CATEGORY_ID = 1482309197814038538     # 裏個室

# 性別ロール（裏QMの「異性にだけ見える」に使用）
MALE_ROLE_ID = 1482301549353897984
FEMALE_ROLE_ID = 1523690396515962981

# Bot管理者ロール。0の場合は「管理者」権限のみ
BOT_ADMIN_ROLE_ID = 1482306904918327336

# 裏募集機能
RECRUIT_CREATE_PANEL_CHANNEL_ID = 1524090558518132907
RECRUIT_CHECK_PANEL_CHANNEL_ID = 1529514190497386606
RECRUIT_NOTIFICATION_CHANNEL_ID = 1521066957103693965
RECRUIT_LOG_CHANNEL_ID = 1529623100986097764

MALE_NOTIFICATION_ROLE_ID = 1482301549353897984
FEMALE_NOTIFICATION_ROLE_ID = 1523690396515962981
ANONYMOUS_NOTIFICATION_ROLE_ID = 1529620347157217331
NAMED_NOTIFICATION_ROLE_ID = 1529620408951771306

# 時間制部屋
TIMED_ROOM_SECONDS = 10 * 60

# 空室になってから削除するまで
EMPTY_DELETE_SECONDS = 15

DATABASE_PATH = "room_bot.sqlite3"

# =========================================================
# ログ
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("room-bot")

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
                timer_started INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS recruit_profiles (
                user_id INTEGER PRIMARY KEY,
                age TEXT NOT NULL DEFAULT '',
                voice TEXT NOT NULL DEFAULT '',
                personality TEXT NOT NULL DEFAULT '',
                attribute TEXT NOT NULL DEFAULT '',
                playstyle TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS recruitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_anonymous INTEGER NOT NULL DEFAULT 1,
                mylist_only INTEGER NOT NULL DEFAULT 0,
                age_filter TEXT NOT NULL DEFAULT '',
                voice_filter TEXT NOT NULL DEFAULT '',
                personality_filter TEXT NOT NULL DEFAULT '',
                attribute_filter TEXT NOT NULL DEFAULT '',
                playstyle_filter TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                notification_message_id INTEGER,
                review_thread_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS recruit_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recruitment_id INTEGER NOT NULL,
                applicant_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                panel_message_id INTEGER,
                panel_channel_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(recruitment_id, applicant_id)
            );
            """
        )


def add_favorite(owner_id: int, target_id: int) -> bool:
    try:
        with db_connect() as con:
            con.execute(
                "INSERT INTO favorites(owner_id, target_id) VALUES (?, ?)",
                (owner_id, target_id),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_favorite(owner_id: int, target_id: int) -> bool:
    with db_connect() as con:
        cur = con.execute(
            "DELETE FROM favorites WHERE owner_id=? AND target_id=?",
            (owner_id, target_id),
        )
        return cur.rowcount > 0


def get_favorites(owner_id: int) -> list[int]:
    with db_connect() as con:
        rows = con.execute(
            "SELECT target_id FROM favorites WHERE owner_id=? ORDER BY target_id",
            (owner_id,),
        ).fetchall()
    return [int(r["target_id"]) for r in rows]


def add_blacklist(owner_id: int, target_id: int) -> bool:
    try:
        with db_connect() as con:
            con.execute(
                "INSERT INTO blacklist(owner_id, target_id) VALUES (?, ?)",
                (owner_id, target_id),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_blacklist(owner_id: int, target_id: int) -> bool:
    with db_connect() as con:
        cur = con.execute(
            "DELETE FROM blacklist WHERE owner_id=? AND target_id=?",
            (owner_id, target_id),
        )
        return cur.rowcount > 0


def get_blacklist(owner_id: int) -> list[int]:
    with db_connect() as con:
        rows = con.execute(
            "SELECT target_id FROM blacklist WHERE owner_id=? ORDER BY target_id",
            (owner_id,),
        ).fetchall()
    return [int(r["target_id"]) for r in rows]


def is_blocked_either_way(user_a: int, user_b: int) -> bool:
    with db_connect() as con:
        row = con.execute(
            """
            SELECT 1
            FROM blacklist
            WHERE (owner_id=? AND target_id=?)
               OR (owner_id=? AND target_id=?)
            LIMIT 1
            """,
            (user_a, user_b, user_b, user_a),
        ).fetchone()
    return row is not None


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
            (
                channel_id,
                guild_id,
                owner_id,
                room_type,
                int(hidden_when_two),
                int(timed),
            ),
        )


def delete_room_record(channel_id: int) -> None:
    with db_connect() as con:
        con.execute("DELETE FROM rooms WHERE channel_id=?", (channel_id,))


def set_timer_started(channel_id: int, started: bool) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE rooms SET timer_started=? WHERE channel_id=?",
            (int(started), channel_id),
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
    knock: bool = False


def clean_channel_name(text: str) -> str:
    forbidden = "/\\#,:`"
    for char in forbidden:
        text = text.replace(char, " ")
    return " ".join(text.split())[:90] or "個室"


def is_bot_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return bool(BOT_ADMIN_ROLE_ID and member.get_role(BOT_ADMIN_ROLE_ID))


def get_category(guild: discord.Guild, category_id: int) -> Optional[discord.CategoryChannel]:
    channel = guild.get_channel(category_id)
    return channel if isinstance(channel, discord.CategoryChannel) else None


def base_bot_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        move_members=True,
        manage_channels=True,
        manage_permissions=True,
    )


async def safe_set_voice_status(channel: discord.VoiceChannel, status: str) -> None:
    """
    discord.pyのバージョンによってはVoiceChannel.set_statusが未実装です。
    利用可能な場合だけ実行し、失敗しても部屋作成は継続します。
    """
    setter = getattr(channel, "set_status", None)
    if setter is None:
        return
    try:
        await setter(status=status[:500])
    except Exception:
        log.exception("VCステータス設定に失敗: channel=%s", channel.id)


def blocked_members_for_owner(guild: discord.Guild, owner_id: int) -> list[discord.Member]:
    ids = set(get_blacklist(owner_id))
    # 相手側からownerがブロックされている場合も除外
    with db_connect() as con:
        rows = con.execute(
            "SELECT owner_id FROM blacklist WHERE target_id=?",
            (owner_id,),
        ).fetchall()
    ids.update(int(r["owner_id"]) for r in rows)

    members: list[discord.Member] = []
    for user_id in ids:
        member = guild.get_member(user_id)
        if member:
            members.append(member)
    return members


async def create_room(
    interaction: discord.Interaction,
    spec: RoomSpec,
    *,
    allowed_members: Optional[list[discord.Member]] = None,
    status: Optional[str] = None,
) -> Optional[discord.VoiceChannel]:
    guild = interaction.guild
    owner = interaction.user

    if guild is None or not isinstance(owner, discord.Member):
        await interaction.followup.send("サーバー内で使用してください。", ephemeral=True)
        return None

    category = get_category(guild, spec.category_id)
    if category is None:
        await interaction.followup.send(
            f"作成先カテゴリID `{spec.category_id}` が設定されていません。",
            ephemeral=True,
        )
        return None

    allowed_members = allowed_members or []
    # 重複排除
    unique: dict[int, discord.Member] = {owner.id: owner}
    for member in allowed_members:
        if not member.bot:
            unique[member.id] = member
    allowed_members = list(unique.values())

    everyone = guild.default_role
    bot_member = guild.me
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {}

    if spec.public_view:
        overwrites[everyone] = discord.PermissionOverwrite(
            view_channel=True,
            connect=False,
        )
    else:
        overwrites[everyone] = discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
        )

    if bot_member:
        overwrites[bot_member] = base_bot_overwrite()

    # 作成者
    overwrites[owner] = discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
        use_voice_activation=True,
        manage_channels=True,
        move_members=True,
    )

    # 指定メンバー
    for member in allowed_members:
        if member.id == owner.id:
            continue
        if is_blocked_either_way(owner.id, member.id):
            continue
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
        )

    # 裏QM：作成者の異性ロールだけ許可
    if spec.opposite_gender_only:
        male_role = guild.get_role(MALE_ROLE_ID) if MALE_ROLE_ID else None
        female_role = guild.get_role(FEMALE_ROLE_ID) if FEMALE_ROLE_ID else None

        if male_role and female_role:
            owner_is_male = male_role in owner.roles
            owner_is_female = female_role in owner.roles

            if owner_is_male:
                overwrites[female_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                )
                overwrites[male_role] = discord.PermissionOverwrite(
                    view_channel=False,
                    connect=False,
                )
            elif owner_is_female:
                overwrites[male_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                )
                overwrites[female_role] = discord.PermissionOverwrite(
                    view_channel=False,
                    connect=False,
                )

    # ブラックリストは常に明示拒否
    for blocked in blocked_members_for_owner(guild, owner.id):
        overwrites[blocked] = discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
        )

    try:
        channel = await guild.create_voice_channel(
            name=clean_channel_name(spec.name),
            category=category,
            overwrites=overwrites,
            user_limit=spec.user_limit,
            reason=f"個室Bot: {owner} が {spec.room_type} を作成",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "部屋を作れませんでした。Botに「チャンネル管理」「権限管理」「メンバーを移動」の権限を付けてください。",
            ephemeral=True,
        )
        return None
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f"Discord APIエラーで作成できませんでした: `{exc}`",
            ephemeral=True,
        )
        return None

    save_room(
        channel.id,
        guild.id,
        owner.id,
        spec.room_type,
        spec.hidden_when_two,
        spec.timed,
    )

    if status:
        await safe_set_voice_status(channel, status)

    await interaction.followup.send(
        f"✅ {channel.mention} を作成しました。",
        ephemeral=True,
    )
    return channel


# =========================================================
# 選択UI
# =========================================================

class QuickOptionSelect(discord.ui.Select):
    def __init__(self, room_kind: str):
        self.room_kind = room_kind
        options: list[discord.SelectOption]

        if room_kind == "public_mylist":
            options = [
                discord.SelectOption(label="通常部屋", value="normal", emoji="🌱"),
            ]
        elif room_kind == "private_mylist":
            options = [
                discord.SelectOption(label="通常部屋", value="normal", emoji="📚"),
                discord.SelectOption(label="ノック部屋", value="knock", emoji="🚪"),
                discord.SelectOption(label="添い寝部屋", value="sleep", emoji="💤"),
                discord.SelectOption(label="ノック添い寝部屋", value="knock_sleep", emoji="🌙"),
            ]
        elif room_kind == "public_qm":
            options = [
                discord.SelectOption(label="新規開拓", value="new", emoji="🌱"),
                discord.SelectOption(label="作業", value="work", emoji="🛠️"),
                discord.SelectOption(label="ゲーム", value="game", emoji="🎮"),
            ]
        elif room_kind == "sleep":
            options = [
                discord.SelectOption(label="通常・同性OK", value="normal_ok", emoji="💤"),
                discord.SelectOption(label="通常・同性NG", value="normal_ng", emoji="🌙"),
                discord.SelectOption(label="ノック・同性OK", value="knock_ok", emoji="🚪"),
                discord.SelectOption(label="ノック・同性NG", value="knock_ng", emoji="🔒"),
            ]
        elif room_kind == "eroip":
            options = [
                discord.SelectOption(label="待機（ノックなし）", value="normal", emoji="🔞"),
                discord.SelectOption(label="ノックあり", value="knock", emoji="🚪"),
            ]
        elif room_kind == "private_qm":
            options = [
                discord.SelectOption(label="同性OK・ノックなし", value="ok_normal", emoji="🌙"),
                discord.SelectOption(label="同性NG・ノックなし", value="ng_normal", emoji="🌙"),
                discord.SelectOption(label="同性OK・ノックあり", value="ok_knock", emoji="🚪"),
                discord.SelectOption(label="同性NG・ノックあり", value="ng_knock", emoji="🔒"),
            ]
        else:
            options = [discord.SelectOption(label="通常", value="normal")]

        super().__init__(
            placeholder="部屋タイプを選択してください",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        value = self.values[0]
        member = interaction.user
        if not isinstance(member, discord.Member):
            return

        owner_name = member.display_name

        if self.room_kind == "public_mylist":
            favorite_ids = get_favorites(member.id)
            favorites = [
                interaction.guild.get_member(uid)
                for uid in favorite_ids
                if interaction.guild and interaction.guild.get_member(uid)
            ]
            favorites = [
                m for m in favorites
                if m and not is_blocked_either_way(member.id, m.id)
            ]
            if not favorites:
                await interaction.followup.send(
                    "マイリストに登録済みのメンバーがいません。",
                    ephemeral=True,
                )
                return
            spec = RoomSpec(
                name=f"🌱｜{owner_name}の表マイリスト",
                category_id=QUICK_PUBLIC_CATEGORY_ID,
                room_type="public_mylist",
                public_view=False,
            )
            await create_room(interaction, spec, allowed_members=favorites)
            return

        if self.room_kind == "private_mylist":
            favorite_ids = get_favorites(member.id)
            favorites = [
                interaction.guild.get_member(uid)
                for uid in favorite_ids
                if interaction.guild and interaction.guild.get_member(uid)
            ]
            favorites = [
                m for m in favorites
                if m and not is_blocked_either_way(member.id, m.id)
            ]
            if not favorites:
                await interaction.followup.send(
                    "マイリストに登録済みのメンバーがいません。",
                    ephemeral=True,
                )
                return
            labels = {
                "normal": "通常",
                "knock": "ノック",
                "sleep": "添い寝",
                "knock_sleep": "ノック添い寝",
            }
            spec = RoomSpec(
                name=f"📚｜{labels[value]}｜{owner_name}",
                category_id=QUICK_PRIVATE_CATEGORY_ID,
                room_type=f"private_mylist_{value}",
                public_view=False,
                hidden_when_two=True,
                knock="knock" in value,
            )
            await create_room(interaction, spec, allowed_members=favorites)
            return

        if self.room_kind == "public_qm":
            names = {
                "new": "🌱｜新規開拓",
                "work": "🛠️｜作業",
                "game": "🎮｜ゲーム",
            }
            statuses = {
                "new": "新規開拓・誰でもどうぞ",
                "work": "作業内容をチャンネルステータスに入力してください",
                "game": "ゲームタイトルをチャンネルステータスに入力してください",
            }
            spec = RoomSpec(
                name=f"{names[value]}｜{owner_name}",
                category_id=QUICK_PUBLIC_CATEGORY_ID,
                room_type=f"public_qm_{value}",
                public_view=True,
            )
            await create_room(interaction, spec, status=statuses[value])
            return

        if self.room_kind == "sleep":
            knock = value.startswith("knock")
            same_ok = value.endswith("_ok")
            spec = RoomSpec(
                name=f"💤｜{'ノック' if knock else '通常'}・{'同性OK' if same_ok else '同性NG'}｜{owner_name}",
                category_id=QUICK_PRIVATE_CATEGORY_ID,
                room_type=f"sleep_{value}",
                public_view=True,
                hidden_when_two=True,
                knock=knock,
            )
            await create_room(
                interaction,
                spec,
                status="声かけ⭕️ または 声かけ❌ を入力してください",
            )
            return

        if self.room_kind == "eroip":
            knock = value == "knock"
            spec = RoomSpec(
                name=f"🔞｜{'ノック' if knock else '待機'}｜{owner_name}",
                category_id=QUICK_PRIVATE_CATEGORY_ID,
                room_type=f"eroip_{value}",
                public_view=True,
                hidden_when_two=True,
                knock=knock,
            )
            await create_room(interaction, spec)
            return

        if self.room_kind == "private_qm":
            knock = value.endswith("knock")
            same_ok = value.startswith("ok")
            spec = RoomSpec(
                name=f"🌙｜{'同性OK' if same_ok else '同性NG'}・{'ノック' if knock else '通常'}｜{owner_name}",
                category_id=QUICK_PRIVATE_CATEGORY_ID,
                room_type=f"private_qm_{value}",
                public_view=False,
                hidden_when_two=True,
                opposite_gender_only=not same_ok,
                knock=knock,
            )
            await create_room(interaction, spec)
            return


class QuickOptionView(discord.ui.View):
    def __init__(self, room_kind: str):
        super().__init__(timeout=120)
        self.add_item(QuickOptionSelect(room_kind))


class QuickPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="表マイリスト部屋",
        emoji="🌱",
        style=discord.ButtonStyle.success,
        custom_id="room:quick:public_mylist",
        row=0,
    )
    async def public_mylist(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "作成タイプを選択してください。",
            view=QuickOptionView("public_mylist"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="裏マイリスト部屋",
        emoji="📚",
        style=discord.ButtonStyle.primary,
        custom_id="room:quick:private_mylist",
        row=0,
    )
    async def private_mylist(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "作成タイプを選択してください。",
            view=QuickOptionView("private_mylist"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="表QM部屋",
        emoji="🌱",
        style=discord.ButtonStyle.success,
        custom_id="room:quick:public_qm",
        row=1,
    )
    async def public_qm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "作成タイプを選択してください。",
            view=QuickOptionView("public_qm"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="表時間制部屋",
        emoji="⏰",
        style=discord.ButtonStyle.secondary,
        custom_id="room:quick:public_timed",
        row=1,
    )
    async def public_timed(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        assert isinstance(member, discord.Member)
        spec = RoomSpec(
            name=f"⏰｜表時間制｜{member.display_name}",
            category_id=QUICK_PUBLIC_CATEGORY_ID,
            room_type="public_timed",
            public_view=True,
            timed=True,
            user_limit=2,
        )
        await create_room(interaction, spec)

    @discord.ui.button(
        label="裏時間制部屋",
        emoji="⏰",
        style=discord.ButtonStyle.secondary,
        custom_id="room:quick:private_timed",
        row=1,
    )
    async def private_timed(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        assert isinstance(member, discord.Member)
        spec = RoomSpec(
            name=f"⏰｜裏時間制｜{member.display_name}",
            category_id=QUICK_PRIVATE_CATEGORY_ID,
            room_type="private_timed",
            public_view=True,
            hidden_when_two=True,
            timed=True,
            user_limit=2,
        )
        await create_room(interaction, spec)

    @discord.ui.button(
        label="裏添い寝部屋",
        emoji="💤",
        style=discord.ButtonStyle.primary,
        custom_id="room:quick:sleep",
        row=2,
    )
    async def sleep_room(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "添い寝部屋のタイプを選択してください。",
            view=QuickOptionView("sleep"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="エロイプ部屋",
        emoji="🔞",
        style=discord.ButtonStyle.danger,
        custom_id="room:quick:eroip",
        row=2,
    )
    async def eroip_room(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "部屋タイプを選択してください。",
            view=QuickOptionView("eroip"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="裏QM部屋",
        emoji="🌙",
        style=discord.ButtonStyle.primary,
        custom_id="room:quick:private_qm",
        row=2,
    )
    async def private_qm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "裏QM部屋のタイプを選択してください。",
            view=QuickOptionView("private_qm"),
            ephemeral=True,
        )


class PrivateRoomUserSelect(discord.ui.UserSelect):
    def __init__(self, hidden: bool):
        self.hidden = hidden
        super().__init__(
            placeholder="個室に招待するユーザーを選択",
            min_values=1,
            max_values=10,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        owner = interaction.user
        if not isinstance(owner, discord.Member):
            return

        selected: list[discord.Member] = []
        blocked_names: list[str] = []

        for user in self.values:
            member = interaction.guild.get_member(user.id) if interaction.guild else None
            if not member or member.bot or member.id == owner.id:
                continue
            if is_blocked_either_way(owner.id, member.id):
                blocked_names.append(member.display_name)
                continue
            selected.append(member)

        if not selected:
            await interaction.followup.send(
                "招待できるユーザーが選択されていません。ブラックリスト関係の相手は招待できません。",
                ephemeral=True,
            )
            return

        spec = RoomSpec(
            name=f"{'🚫' if self.hidden else '⭕'}｜{owner.display_name}の個室",
            category_id=(
                PRIVATE_HIDDEN_CATEGORY_ID
                if self.hidden
                else PRIVATE_PUBLIC_CATEGORY_ID
            ),
            room_type="hidden_private" if self.hidden else "public_private",
            public_view=not self.hidden,
        )
        channel = await create_room(interaction, spec, allowed_members=selected)

        if channel and blocked_names:
            await interaction.followup.send(
                "ブラックリスト関係のため除外: " + "、".join(blocked_names),
                ephemeral=True,
            )


class FavoriteRoomSelect(discord.ui.Select):
    def __init__(self, owner: discord.Member, hidden: bool):
        self.owner_id = owner.id
        self.hidden = hidden

        favorite_ids = get_favorites(owner.id)
        options: list[discord.SelectOption] = []

        for user_id in favorite_ids[:25]:
            member = owner.guild.get_member(user_id)
            if not member or member.bot:
                continue
            if is_blocked_either_way(owner.id, member.id):
                continue
            options.append(
                discord.SelectOption(
                    label=member.display_name[:100],
                    value=str(member.id),
                    description=f"ID: {member.id}",
                )
            )

        if not options:
            options = [
                discord.SelectOption(
                    label="選択できるお気に入りがありません",
                    value="none",
                )
            ]

        super().__init__(
            placeholder="お気に入りから選択",
            options=options,
            min_values=1,
            max_values=min(10, len(options)),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この選択画面は作成者本人だけ使用できます。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        if "none" in self.values:
            await interaction.followup.send(
                "先に `/mylist_add` でお気に入りを登録してください。",
                ephemeral=True,
            )
            return

        members: list[discord.Member] = []
        if interaction.guild:
            for value in self.values:
                member = interaction.guild.get_member(int(value))
                if member and not is_blocked_either_way(interaction.user.id, member.id):
                    members.append(member)

        if not members:
            await interaction.followup.send(
                "招待可能なメンバーがいません。",
                ephemeral=True,
            )
            return

        owner = interaction.user
        assert isinstance(owner, discord.Member)

        spec = RoomSpec(
            name=f"{'🚫' if self.hidden else '⭕'}｜{owner.display_name}の個室",
            category_id=(
                PRIVATE_HIDDEN_CATEGORY_ID
                if self.hidden
                else PRIVATE_PUBLIC_CATEGORY_ID
            ),
            room_type="hidden_private" if self.hidden else "public_private",
            public_view=not self.hidden,
        )
        await create_room(interaction, spec, allowed_members=members)


class PrivateCreateChoiceView(discord.ui.View):
    def __init__(self, owner: discord.Member, hidden: bool):
        super().__init__(timeout=120)
        self.owner = owner
        self.hidden = hidden

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "この画面はボタンを押した本人だけ使えます。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="ユーザー選択",
        emoji="👤",
        style=discord.ButtonStyle.primary,
    )
    async def user_select(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        view = discord.ui.View(timeout=120)
        view.add_item(PrivateRoomUserSelect(self.hidden))
        await interaction.response.edit_message(
            content="招待するユーザーを選択してください。",
            view=view,
        )

    @discord.ui.button(
        label="お気に入り選択",
        emoji="⭐",
        style=discord.ButtonStyle.success,
    )
    async def favorite_select(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        view = discord.ui.View(timeout=120)
        view.add_item(FavoriteRoomSelect(self.owner, self.hidden))
        await interaction.response.edit_message(
            content="お気に入りから招待するユーザーを選択してください。",
            view=view,
        )


class PrivatePanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="表個室",
        emoji="⭕",
        style=discord.ButtonStyle.success,
        custom_id="room:private:public",
    )
    async def public_private(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        owner = interaction.user
        if not isinstance(owner, discord.Member):
            return
        await interaction.response.send_message(
            "作成方法を選択してください。\n"
            "表個室は全員が閲覧できますが、接続できるのは作成者と選択した人だけです。",
            view=PrivateCreateChoiceView(owner, hidden=False),
            ephemeral=True,
        )

    @discord.ui.button(
        label="裏個室",
        emoji="🚫",
        style=discord.ButtonStyle.danger,
        custom_id="room:private:hidden",
    )
    async def hidden_private(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        owner = interaction.user
        if not isinstance(owner, discord.Member):
            return
        await interaction.response.send_message(
            "作成方法を選択してください。\n"
            "裏個室は作成者と選択した人だけが閲覧・接続できます。",
            view=PrivateCreateChoiceView(owner, hidden=True),
            ephemeral=True,
        )



# =========================================================
# 裏募集システム
# =========================================================

def normalize_filter_values(value: str) -> set[str]:
    value = value.replace("、", ",").replace("／", ",").replace("/", ",")
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def parse_filter_text(text: str) -> dict[str, str]:
    result = {
        "age": "",
        "voice": "",
        "personality": "",
        "attribute": "",
        "playstyle": "",
    }
    aliases = {
        "年齢": "age",
        "声質": "voice",
        "性格": "personality",
        "属性": "attribute",
        "プレイスタイル": "playstyle",
        "play": "playstyle",
    }

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        separator = "=" if "=" in line else ("：" if "：" in line else (":" if ":" in line else None))
        if not separator:
            continue

        key, value = line.split(separator, 1)
        mapped = aliases.get(key.strip())
        if mapped:
            result[mapped] = value.strip()

    return result


def save_recruit_profile(
    user_id: int,
    age: str,
    voice: str,
    personality: str,
    attribute: str,
    playstyle: str,
) -> None:
    with db_connect() as con:
        con.execute(
            """
            INSERT INTO recruit_profiles
            (user_id, age, voice, personality, attribute, playstyle)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                age=excluded.age,
                voice=excluded.voice,
                personality=excluded.personality,
                attribute=excluded.attribute,
                playstyle=excluded.playstyle
            """,
            (user_id, age, voice, personality, attribute, playstyle),
        )


def get_recruit_profile(user_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM recruit_profiles WHERE user_id=?",
            (user_id,),
        ).fetchone()


def get_active_recruitment_by_owner(owner_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            """
            SELECT * FROM recruitments
            WHERE owner_id=? AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (owner_id,),
        ).fetchone()


def get_recruitment(recruitment_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM recruitments WHERE id=?",
            (recruitment_id,),
        ).fetchone()


def get_active_recruitments() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            """
            SELECT * FROM recruitments
            WHERE status='active'
            ORDER BY id DESC
            """
        ).fetchall()


def create_recruitment_record(
    guild_id: int,
    owner_id: int,
    content: str,
    is_anonymous: bool,
    mylist_only: bool,
    filters: dict[str, str],
) -> int:
    with db_connect() as con:
        cur = con.execute(
            """
            INSERT INTO recruitments
            (
                guild_id, owner_id, content, is_anonymous, mylist_only,
                age_filter, voice_filter, personality_filter,
                attribute_filter, playstyle_filter
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                owner_id,
                content,
                int(is_anonymous),
                int(mylist_only),
                filters["age"],
                filters["voice"],
                filters["personality"],
                filters["attribute"],
                filters["playstyle"],
            ),
        )
        return int(cur.lastrowid)


def update_recruitment_publish_info(
    recruitment_id: int,
    message_id: int,
) -> None:
    with db_connect() as con:
        con.execute(
            """
            UPDATE recruitments
            SET notification_message_id=?
            WHERE id=?
            """,
            (message_id, recruitment_id),
        )


def update_recruitment_review_thread(
    recruitment_id: int,
    thread_id: int,
) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE recruitments SET review_thread_id=? WHERE id=?",
            (thread_id, recruitment_id),
        )


def close_recruitment(recruitment_id: int, status: str = "closed") -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE recruitments SET status=? WHERE id=?",
            (status, recruitment_id),
        )


def create_application_record(recruitment_id: int, applicant_id: int) -> Optional[int]:
    try:
        with db_connect() as con:
            cur = con.execute(
                """
                INSERT INTO recruit_applications(recruitment_id, applicant_id)
                VALUES (?, ?)
                """,
                (recruitment_id, applicant_id),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None


def get_application(application_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            "SELECT * FROM recruit_applications WHERE id=?",
            (application_id,),
        ).fetchone()


def set_application_panel(
    application_id: int,
    message_id: int,
    channel_id: int,
) -> None:
    with db_connect() as con:
        con.execute(
            """
            UPDATE recruit_applications
            SET panel_message_id=?, panel_channel_id=?
            WHERE id=?
            """,
            (message_id, channel_id, application_id),
        )


def update_application_status(application_id: int, status: str) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE recruit_applications SET status=? WHERE id=?",
            (status, application_id),
        )


def get_pending_applications() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute(
            """
            SELECT * FROM recruit_applications
            WHERE status='pending' AND panel_message_id IS NOT NULL
            """
        ).fetchall()


def member_gender(member: discord.Member) -> Optional[str]:
    if member.get_role(MALE_ROLE_ID):
        return "male"
    if member.get_role(FEMALE_ROLE_ID):
        return "female"
    return None


def is_opposite_gender(owner: discord.Member, viewer: discord.Member) -> bool:
    owner_gender = member_gender(owner)
    viewer_gender = member_gender(viewer)
    return bool(owner_gender and viewer_gender and owner_gender != viewer_gender)


def profile_matches_recruitment(
    profile: Optional[sqlite3.Row],
    recruitment: sqlite3.Row,
) -> bool:
    mapping = [
        ("age", "age_filter"),
        ("voice", "voice_filter"),
        ("personality", "personality_filter"),
        ("attribute", "attribute_filter"),
        ("playstyle", "playstyle_filter"),
    ]

    for profile_key, filter_key in mapping:
        wanted = normalize_filter_values(str(recruitment[filter_key] or ""))
        if not wanted:
            continue
        if profile is None:
            return False
        owned = normalize_filter_values(str(profile[profile_key] or ""))
        if not wanted.intersection(owned):
            return False

    return True


def can_view_recruitment(
    guild: discord.Guild,
    viewer: discord.Member,
    recruitment: sqlite3.Row,
) -> bool:
    owner = guild.get_member(int(recruitment["owner_id"]))
    if owner is None or owner.id == viewer.id:
        return False

    if not is_opposite_gender(owner, viewer):
        return False

    if is_blocked_either_way(owner.id, viewer.id):
        return False

    if bool(recruitment["mylist_only"]):
        if viewer.id not in get_favorites(owner.id):
            return False

    return profile_matches_recruitment(
        get_recruit_profile(viewer.id),
        recruitment,
    )


def recruitment_filter_summary(row: sqlite3.Row) -> str:
    lines = []
    labels = [
        ("年齢", "age_filter"),
        ("声質", "voice_filter"),
        ("性格", "personality_filter"),
        ("属性", "attribute_filter"),
        ("プレイスタイル", "playstyle_filter"),
    ]
    for label, key in labels:
        value = str(row[key] or "").strip()
        if value:
            lines.append(f"**{label}：** {value}")
    return "\n".join(lines) if lines else "指定なし"


async def send_recruit_log(guild: discord.Guild, text: str) -> None:
    channel = guild.get_channel(RECRUIT_LOG_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(text)
        except discord.HTTPException:
            log.exception("裏募集ログの送信に失敗")


async def notify_matching_members(
    guild: discord.Guild,
    recruitment: sqlite3.Row,
) -> int:
    owner = guild.get_member(int(recruitment["owner_id"]))
    if owner is None:
        return 0

    owner_gender = member_gender(owner)
    target_gender_role_id = (
        FEMALE_NOTIFICATION_ROLE_ID
        if owner_gender == "male"
        else MALE_NOTIFICATION_ROLE_ID
    )
    mode_role_id = (
        ANONYMOUS_NOTIFICATION_ROLE_ID
        if bool(recruitment["is_anonymous"])
        else NAMED_NOTIFICATION_ROLE_ID
    )

    notified = 0
    for member in guild.members:
        if member.bot:
            continue
        if member.get_role(target_gender_role_id) is None:
            continue
        if member.get_role(mode_role_id) is None:
            continue
        if not can_view_recruitment(guild, member, recruitment):
            continue

        try:
            mode = "匿名" if bool(recruitment["is_anonymous"]) else "記名"
            await member.send(
                f"🌙 **新しいフィルタリング付き裏募集があります**\n"
                f"募集形式：{mode}\n"
                f"サーバー：{guild.name}\n\n"
                f"募集確認パネルから内容を確認してください。"
            )
            notified += 1
        except (discord.Forbidden, discord.HTTPException):
            continue

    return notified


class RecruitProfileModal(discord.ui.Modal, title="裏募集プロフィール設定"):
    age = discord.ui.TextInput(
        label="年齢",
        placeholder="例：20代後半, 30代",
        required=False,
        max_length=100,
    )
    voice = discord.ui.TextInput(
        label="声質",
        placeholder="例：低音, 中音",
        required=False,
        max_length=100,
    )
    personality = discord.ui.TextInput(
        label="性格",
        placeholder="例：まったり, 積極的",
        required=False,
        max_length=100,
    )
    attribute = discord.ui.TextInput(
        label="属性",
        placeholder="例：甘えたい, 甘えられたい",
        required=False,
        max_length=100,
    )
    playstyle = discord.ui.TextInput(
        label="プレイスタイル",
        placeholder="例：雑談中心, 添い寝, エロイプ",
        required=False,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        save_recruit_profile(
            interaction.user.id,
            str(self.age),
            str(self.voice),
            str(self.personality),
            str(self.attribute),
            str(self.playstyle),
        )
        await interaction.response.send_message(
            "✅ 裏募集プロフィールを保存しました。",
            ephemeral=True,
        )


class RecruitCreateModal(discord.ui.Modal, title="フィルタリング付き裏募集"):
    content = discord.ui.TextInput(
        label="募集文章",
        style=discord.TextStyle.paragraph,
        placeholder="募集内容を入力してください。",
        required=True,
        min_length=2,
        max_length=1500,
    )
    filters = discord.ui.TextInput(
        label="フィルター（空欄可）",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "年齢=20代後半,30代\n"
            "声質=低音,中音\n"
            "性格=まったり\n"
            "属性=甘えたい\n"
            "プレイスタイル=雑談中心"
        ),
        required=False,
        max_length=800,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parsed = parse_filter_text(str(self.filters))
        await interaction.response.send_message(
            "公開前の設定を選んでください。",
            embed=discord.Embed(
                title="🌙 裏募集プレビュー",
                description=str(self.content),
                color=discord.Color.dark_purple(),
            ).add_field(
                name="フィルター",
                value=(
                    "\n".join(
                        f"**{label}：** {parsed[key]}"
                        for label, key in [
                            ("年齢", "age"),
                            ("声質", "voice"),
                            ("性格", "personality"),
                            ("属性", "attribute"),
                            ("プレイスタイル", "playstyle"),
                        ]
                        if parsed[key]
                    )
                    or "指定なし"
                ),
                inline=False,
            ),
            view=RecruitDraftView(
                owner_id=interaction.user.id,
                content=str(self.content),
                filters=parsed,
            ),
            ephemeral=True,
        )


class RecruitDraftView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        content: str,
        filters: dict[str, str],
    ):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.content = content
        self.filters = filters
        self.is_anonymous = True
        self.mylist_only = False
        self.refresh_labels()

    def refresh_labels(self) -> None:
        self.mode_button.label = "募集主：匿名" if self.is_anonymous else "募集主：記名"
        self.list_button.label = (
            "募集先：マイリストのみ"
            if self.mylist_only
            else "募集先：条件一致の異性"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この設定画面は募集作成者だけ使用できます。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="募集主：匿名",
        emoji="🥷",
        style=discord.ButtonStyle.secondary,
    )
    async def mode_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.is_anonymous = not self.is_anonymous
        self.refresh_labels()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(
        label="募集先：条件一致の異性",
        emoji="⭐",
        style=discord.ButtonStyle.secondary,
    )
    async def list_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.mylist_only = not self.mylist_only
        self.refresh_labels()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(
        label="公開",
        emoji="📢",
        style=discord.ButtonStyle.success,
    )
    async def publish_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        if get_active_recruitment_by_owner(member.id):
            await interaction.followup.send(
                "現在公開中の募集があります。先に削除してください。",
                ephemeral=True,
            )
            return

        if member_gender(member) is None:
            await interaction.followup.send(
                "男性ロールまたは女性ロールが付いていないため募集できません。",
                ephemeral=True,
            )
            return

        recruitment_id = create_recruitment_record(
            guild.id,
            member.id,
            self.content,
            self.is_anonymous,
            self.mylist_only,
            self.filters,
        )
        row = get_recruitment(recruitment_id)
        assert row is not None

        channel = guild.get_channel(RECRUIT_NOTIFICATION_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            close_recruitment(recruitment_id, "error")
            await interaction.followup.send(
                "新規募集通知チャンネルが見つかりません。",
                ephemeral=True,
            )
            return

        mode = "匿名" if self.is_anonymous else "記名"
        audience = "マイリストのみ" if self.mylist_only else "条件一致の異性"
        notification = await channel.send(
            embed=discord.Embed(
                title="🌙 新しい裏募集があります",
                description=(
                    f"募集形式：**{mode}**\n"
                    f"募集先：**{audience}**\n\n"
                    "内容は裏募集確認パネルから確認できます。"
                ),
                color=discord.Color.dark_purple(),
            )
        )
        update_recruitment_publish_info(recruitment_id, notification.id)

        notified = await notify_matching_members(guild, row)
        await send_recruit_log(
            guild,
            f"📢 裏募集 #{recruitment_id} を {member} が公開しました。"
            f"形式={mode} / DM通知={notified}人",
        )

        await interaction.followup.send(
            f"✅ 裏募集を公開しました。\n条件一致DM通知：**{notified}人**",
            ephemeral=True,
        )
        self.stop()


class RecruitCreatePanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="裏募集を作成",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="recruit:create",
    )
    async def create_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if get_active_recruitment_by_owner(interaction.user.id):
            await interaction.response.send_message(
                "現在公開中の募集があります。削除してから新しく作成してください。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RecruitCreateModal())

    @discord.ui.button(
        label="プロフィール設定",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="recruit:profile",
    )
    async def profile_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(RecruitProfileModal())

    @discord.ui.button(
        label="自分の募集を確認",
        emoji="🔍",
        style=discord.ButtonStyle.secondary,
        custom_id="recruit:mine",
    )
    async def mine_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        row = get_active_recruitment_by_owner(interaction.user.id)
        if row is None:
            await interaction.response.send_message(
                "現在公開中の募集はありません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=build_recruitment_embed(
                interaction.guild,
                row,
                reveal_owner=True,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="募集を削除",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="recruit:delete",
    )
    async def delete_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        row = get_active_recruitment_by_owner(interaction.user.id)
        if row is None:
            await interaction.response.send_message(
                "削除できる募集はありません。",
                ephemeral=True,
            )
            return

        close_recruitment(int(row["id"]), "deleted")
        if interaction.guild:
            await send_recruit_log(
                interaction.guild,
                f"🗑️ 裏募集 #{row['id']} を {interaction.user} が削除しました。",
            )
        await interaction.response.send_message(
            "✅ 募集を削除しました。",
            ephemeral=True,
        )


def build_recruitment_embed(
    guild: Optional[discord.Guild],
    row: sqlite3.Row,
    reveal_owner: bool = False,
) -> discord.Embed:
    anonymous = bool(row["is_anonymous"])
    owner = guild.get_member(int(row["owner_id"])) if guild else None

    if anonymous and not reveal_owner:
        owner_text = "匿名"
    else:
        owner_text = owner.mention if owner else f"ユーザーID：{row['owner_id']}"

    embed = discord.Embed(
        title=f"🌙 裏募集 #{row['id']}",
        description=str(row["content"]),
        color=discord.Color.dark_purple(),
    )
    embed.add_field(name="募集主", value=owner_text, inline=True)
    embed.add_field(
        name="募集範囲",
        value="マイリストのみ" if bool(row["mylist_only"]) else "条件一致の異性",
        inline=True,
    )
    embed.add_field(
        name="フィルター",
        value=recruitment_filter_summary(row),
        inline=False,
    )
    return embed


class RecruitmentSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, viewer: discord.Member):
        rows = [
            row
            for row in get_active_recruitments()
            if can_view_recruitment(guild, viewer, row)
        ][:25]
        self.rows_by_id = {int(row["id"]): row for row in rows}

        options = []
        for row in rows:
            content = str(row["content"]).replace("\n", " ")
            options.append(
                discord.SelectOption(
                    label=f"裏募集 #{row['id']}",
                    value=str(row["id"]),
                    description=content[:100],
                    emoji="🌙",
                )
            )

        super().__init__(
            placeholder="確認する募集を選択してください",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        recruitment_id = int(self.values[0])
        row = get_recruitment(recruitment_id)
        member = interaction.user

        if (
            row is None
            or interaction.guild is None
            or not isinstance(member, discord.Member)
            or not can_view_recruitment(interaction.guild, member, row)
        ):
            await interaction.response.send_message(
                "この募集は確認できないか、すでに終了しています。",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=None,
            embed=build_recruitment_embed(interaction.guild, row),
            view=RecruitApplyView(recruitment_id),
        )


class RecruitCheckPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="裏募集確認",
        emoji="🌙",
        style=discord.ButtonStyle.primary,
        custom_id="recruit:check",
    )
    async def check_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        matches = [
            row for row in get_active_recruitments()
            if can_view_recruitment(guild, member, row)
        ]
        if not matches:
            await interaction.response.send_message(
                "現在、あなたが確認できる裏募集はありません。",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=180)
        view.add_item(RecruitmentSelect(guild, member))
        await interaction.response.send_message(
            "確認する裏募集を選択してください。",
            view=view,
            ephemeral=True,
        )


class RecruitApplyView(discord.ui.View):
    def __init__(self, recruitment_id: int):
        super().__init__(timeout=180)
        self.recruitment_id = recruitment_id

    @discord.ui.button(
        label="立候補する",
        emoji="🙋",
        style=discord.ButtonStyle.success,
    )
    async def apply_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        applicant = interaction.user
        row = get_recruitment(self.recruitment_id)

        if (
            guild is None
            or not isinstance(applicant, discord.Member)
            or row is None
            or not can_view_recruitment(guild, applicant, row)
        ):
            await interaction.followup.send(
                "この募集には立候補できません。",
                ephemeral=True,
            )
            return

        application_id = create_application_record(
            self.recruitment_id,
            applicant.id,
        )
        if application_id is None:
            await interaction.followup.send(
                "この募集にはすでに立候補済みです。",
                ephemeral=True,
            )
            return

        owner = guild.get_member(int(row["owner_id"]))
        notification_channel = guild.get_channel(RECRUIT_NOTIFICATION_CHANNEL_ID)
        if owner is None or not isinstance(notification_channel, discord.TextChannel):
            update_application_status(application_id, "error")
            await interaction.followup.send(
                "立候補パネルを作成できませんでした。",
                ephemeral=True,
            )
            return

        thread = None
        existing_thread_id = row["review_thread_id"]
        if existing_thread_id:
            possible = guild.get_channel(int(existing_thread_id))
            if isinstance(possible, discord.Thread):
                thread = possible

        if thread is None:
            thread = await notification_channel.create_thread(
                name=f"募集#{self.recruitment_id}-立候補確認",
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason="裏募集の立候補確認スレッド",
            )
            await thread.add_user(owner)
            update_recruitment_review_thread(self.recruitment_id, thread.id)

        panel = await thread.send(
            content=owner.mention,
            embed=discord.Embed(
                title=f"🙋 裏募集 #{self.recruitment_id} へ立候補",
                description=f"{applicant.mention} さんから立候補が届きました。",
                color=discord.Color.gold(),
            ),
            view=ApplicationDecisionView(application_id),
        )
        set_application_panel(application_id, panel.id, thread.id)

        await send_recruit_log(
            guild,
            f"🙋 {applicant} が裏募集 #{self.recruitment_id} に立候補しました。",
        )
        await interaction.followup.send(
            "✅ 立候補を送信しました。",
            ephemeral=True,
        )


class ApplicationDecisionView(discord.ui.View):
    def __init__(self, application_id: int):
        super().__init__(timeout=None)
        self.application_id = application_id

        self.accept_button.custom_id = f"recruit:accept:{application_id}"
        self.reject_button.custom_id = f"recruit:reject:{application_id}"

    async def validate_owner(
        self,
        interaction: discord.Interaction,
    ) -> tuple[Optional[sqlite3.Row], Optional[sqlite3.Row]]:
        application = get_application(self.application_id)
        if application is None:
            return None, None
        recruitment = get_recruitment(int(application["recruitment_id"]))
        if recruitment is None or interaction.user.id != int(recruitment["owner_id"]):
            await interaction.response.send_message(
                "募集主本人だけ操作できます。",
                ephemeral=True,
            )
            return None, None
        return application, recruitment

    @discord.ui.button(
        label="おっけー",
        emoji="⭕",
        style=discord.ButtonStyle.success,
        custom_id="recruit:accept:placeholder",
    )
    async def accept_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        application, recruitment = await self.validate_owner(interaction)
        if application is None or recruitment is None:
            return
        if str(application["status"]) != "pending":
            await interaction.response.send_message(
                "この立候補はすでに処理済みです。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if guild is None:
            return

        applicant = guild.get_member(int(application["applicant_id"]))
        owner = guild.get_member(int(recruitment["owner_id"]))
        channel = guild.get_channel(RECRUIT_NOTIFICATION_CHANNEL_ID)

        if (
            applicant is None
            or owner is None
            or not isinstance(channel, discord.TextChannel)
        ):
            await interaction.followup.send(
                "連絡スレッドを作成できませんでした。",
                ephemeral=True,
            )
            return

        contact_thread = await channel.create_thread(
            name=f"裏募集#{recruitment['id']}-連絡",
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason="裏募集成立後の連絡スレッド",
        )
        await contact_thread.add_user(owner)
        await contact_thread.add_user(applicant)
        await contact_thread.send(
            f"{owner.mention} {applicant.mention}\n"
            "✅ 募集が成立しました。こちらで連絡を取り合ってください。"
        )

        update_application_status(self.application_id, "accepted")
        close_recruitment(int(recruitment["id"]), "matched")

        button.disabled = True
        self.reject_button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        await send_recruit_log(
            guild,
            f"⭕ 裏募集 #{recruitment['id']} が成立しました。"
            f"募集主={owner} / 立候補者={applicant}",
        )
        await interaction.followup.send(
            f"✅ 了承しました。連絡スレッド：{contact_thread.mention}",
            ephemeral=True,
        )

    @discord.ui.button(
        label="お断り",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="recruit:reject:placeholder",
    )
    async def reject_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        application, recruitment = await self.validate_owner(interaction)
        if application is None or recruitment is None:
            return
        if str(application["status"]) != "pending":
            await interaction.response.send_message(
                "この立候補はすでに処理済みです。",
                ephemeral=True,
            )
            return

        update_application_status(self.application_id, "rejected")
        button.disabled = True
        self.accept_button.disabled = True
        await interaction.response.edit_message(view=self)

        if interaction.guild:
            await send_recruit_log(
                interaction.guild,
                f"✖️ 裏募集 #{recruitment['id']} の立候補が断られました。"
                f"立候補者ID={application['applicant_id']}",
            )



# =========================================================
# Bot
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True


class RoomBot(commands.Bot):
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

        # 再起動後もパネルボタンを使えるようにする
        self.add_view(QuickPanel())
        self.add_view(PrivatePanel())
        self.add_view(RecruitCreatePanel())
        self.add_view(RecruitCheckPanel())

        # 再起動前に届いた未処理の立候補ボタンを復元
        for application in get_pending_applications():
            self.add_view(
                ApplicationDecisionView(int(application["id"])),
                message_id=int(application["panel_message_id"]),
            )

        guild_obj = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild_obj)

        try:
            synced = await self.tree.sync(guild=guild_obj)
            log.info(
                "%s個のコマンドをGuild %sへ同期しました",
                len(synced),
                GUILD_ID,
            )
        except discord.Forbidden:
            log.exception(
                "Guildコマンド同期に失敗しました。"
                "RenderのDISCORD_TOKENが、このサーバーに参加しているBotのものか確認してください。"
                "またBotを bot + applications.commands の両方のScopeで招待してください。"
            )
        except discord.HTTPException:
            log.exception("Discord APIエラーによりコマンド同期に失敗しました。")

    async def on_ready(self) -> None:
        log.info("ログインBot: %s", self.user)
        log.info("BotユーザーID: %s", self.user.id if self.user else "?")
        log.info("設定Guild ID: %s", GUILD_ID)

        guild = self.get_guild(GUILD_ID)
        if guild is None:
            log.error(
                "設定したGuildが見つかりません。"
                "このトークンのBotがGuild %sへ参加しているか確認してください。",
                GUILD_ID,
            )
        else:
            log.info("接続Guild: %s (%s)", guild.name, guild.id)

        await self.cleanup_missing_rooms()

    async def cleanup_missing_rooms(self) -> None:
        for row in all_rooms():
            guild = self.get_guild(int(row["guild_id"]))
            channel = guild.get_channel(int(row["channel_id"])) if guild else None
            if not isinstance(channel, discord.VoiceChannel):
                delete_room_record(int(row["channel_id"]))

    async def start_room_timer(self, channel: discord.VoiceChannel) -> None:
        if channel.id in self.timer_tasks:
            return

        set_timer_started(channel.id, True)

        async def runner() -> None:
            try:
                await asyncio.sleep(TIMED_ROOM_SECONDS)
                current = channel.guild.get_channel(channel.id)
                if isinstance(current, discord.VoiceChannel):
                    await current.delete(reason="時間制部屋の制限時間終了")
            except asyncio.CancelledError:
                raise
            except discord.NotFound:
                pass
            except Exception:
                log.exception("時間制部屋の削除失敗: %s", channel.id)
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
                    await current.delete(reason="作成部屋が空室になったため削除")
                    delete_room_record(channel.id)
            except asyncio.CancelledError:
                raise
            except discord.NotFound:
                delete_room_record(channel.id)
            except Exception:
                log.exception("空室削除失敗: %s", channel.id)
            finally:
                self.empty_tasks.pop(channel.id, None)

        self.empty_tasks[channel.id] = asyncio.create_task(runner())

    async def hide_room_for_current_members(
        self,
        channel: discord.VoiceChannel,
        owner_id: int,
    ) -> None:
        # @everyoneから見えなくし、現在VCにいる人と作成者だけ見えるようにする
        await channel.set_permissions(
            channel.guild.default_role,
            view_channel=False,
            connect=False,
            reason="2人揃ったため部屋を非表示",
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
                reason="個室参加者の閲覧・接続を維持",
            )

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        affected_ids = {
            channel.id
            for channel in (before.channel, after.channel)
            if isinstance(channel, discord.VoiceChannel)
        }

        for channel_id in affected_ids:
            row = get_room(channel_id)
            if row is None:
                continue

            channel = member.guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                delete_room_record(channel_id)
                continue

            real_members = [m for m in channel.members if not m.bot]

            # 人が戻ったら空室削除を中止
            if real_members:
                task = self.empty_tasks.pop(channel.id, None)
                if task:
                    task.cancel()
            else:
                await self.schedule_empty_delete(channel)
                continue

            if bool(row["hidden_when_two"]) and len(real_members) >= 2:
                try:
                    await self.hide_room_for_current_members(
                        channel,
                        int(row["owner_id"]),
                    )
                except discord.Forbidden:
                    log.error("非表示化に必要な権限がありません: %s", channel.id)
                except Exception:
                    log.exception("部屋の非表示化失敗: %s", channel.id)

            if bool(row["timed"]) and len(real_members) >= 2:
                await self.start_room_timer(channel)


bot = RoomBot()


# =========================================================
# スラッシュコマンド
# =========================================================

@bot.tree.command(name="setup_quick_panel", description="クイック作成パネルを設置します")
@app_commands.guild_only()
async def setup_quick_panel(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_bot_admin(member):
        await interaction.response.send_message(
            "管理者専用コマンドです。",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="➕ クイック作成",
        description=(
            "作成したい部屋のボタンを押してください。\n\n"
            "🌱 **表マイリスト部屋**\n"
            "マイリスト登録者だけが閲覧・接続できます。\n\n"
            "📚 **裏マイリスト部屋**\n"
            "マイリスト登録者向け。2人揃うと外から見えなくなります。\n\n"
            "🌱 **表QM部屋**\n"
            "新規開拓・作業・ゲームから選択できます。\n\n"
            "⏰ **表／裏時間制部屋**\n"
            "2人揃ってから10分で自動削除されます。\n\n"
            "💤 **裏添い寝部屋**\n"
            "通常／ノック、同性OK／NGを選択できます。\n\n"
            "🔞 **エロイプ部屋**\n"
            "待機またはノックから選択できます。\n\n"
            "🌙 **裏QM部屋**\n"
            "同性OK／NG、ノックあり／なしを選択できます。\n\n"
            "※ブラックリスト関係のユーザーには閲覧・接続権限を付与しません。"
        ),
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=QuickPanel())
    await interaction.response.send_message(
        "✅ クイック作成パネルを設置しました。",
        ephemeral=True,
    )


@bot.tree.command(name="setup_private_panel", description="表個室・裏個室パネルを設置します")
@app_commands.guild_only()
async def setup_private_panel(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_bot_admin(member):
        await interaction.response.send_message(
            "管理者専用コマンドです。",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="➕ 個室作成",
        description=(
            "⭕ **表個室**\n"
            "サーバー内全員が閲覧できます。\n"
            "接続できるのは作成者と選択した人だけです。\n\n"
            "🚫 **裏個室**\n"
            "作成者と選択した人だけが閲覧・接続できます。\n\n"
            "ユーザー選択、またはお気に入り選択から作成できます。"
        ),
        color=discord.Color.green(),
    )
    await interaction.channel.send(embed=embed, view=PrivatePanel())
    await interaction.response.send_message(
        "✅ 個室作成パネルを設置しました。",
        ephemeral=True,
    )


@bot.tree.command(name="mylist_add", description="ユーザーをマイリストに追加します")
@app_commands.describe(user="追加するユーザー")
@app_commands.guild_only()
async def mylist_add(
    interaction: discord.Interaction,
    user: discord.Member,
) -> None:
    if user.bot or user.id == interaction.user.id:
        await interaction.response.send_message(
            "自分自身またはBotは追加できません。",
            ephemeral=True,
        )
        return
    if is_blocked_either_way(interaction.user.id, user.id):
        await interaction.response.send_message(
            "ブラックリスト関係のユーザーは追加できません。",
            ephemeral=True,
        )
        return

    added = add_favorite(interaction.user.id, user.id)
    await interaction.response.send_message(
        (
            f"⭐ {user.mention} をマイリストに追加しました。"
            if added
            else f"{user.mention} はすでにマイリストに入っています。"
        ),
        ephemeral=True,
    )


@bot.tree.command(name="mylist_remove", description="ユーザーをマイリストから削除します")
@app_commands.describe(user="削除するユーザー")
@app_commands.guild_only()
async def mylist_remove(
    interaction: discord.Interaction,
    user: discord.Member,
) -> None:
    removed = remove_favorite(interaction.user.id, user.id)
    await interaction.response.send_message(
        (
            f"🗑️ {user.mention} をマイリストから削除しました。"
            if removed
            else f"{user.mention} はマイリストに登録されていません。"
        ),
        ephemeral=True,
    )


@bot.tree.command(name="mylist_view", description="自分のマイリストを確認します")
@app_commands.guild_only()
async def mylist_view(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    ids = get_favorites(interaction.user.id)
    names: list[str] = []

    if guild:
        for user_id in ids:
            member = guild.get_member(user_id)
            names.append(member.mention if member else f"不明なユーザー ({user_id})")

    text = "\n".join(f"・{name}" for name in names) or "登録されていません。"
    await interaction.response.send_message(
        f"⭐ **あなたのマイリスト**\n{text}",
        ephemeral=True,
    )


@bot.tree.command(name="blacklist_add", description="ユーザーをブラックリストに追加します")
@app_commands.describe(user="追加するユーザー")
@app_commands.guild_only()
async def blacklist_add(
    interaction: discord.Interaction,
    user: discord.Member,
) -> None:
    if user.bot or user.id == interaction.user.id:
        await interaction.response.send_message(
            "自分自身またはBotは追加できません。",
            ephemeral=True,
        )
        return

    added = add_blacklist(interaction.user.id, user.id)
    remove_favorite(interaction.user.id, user.id)

    await interaction.response.send_message(
        (
            f"🚫 {user.mention} をブラックリストに追加しました。"
            if added
            else f"{user.mention} はすでにブラックリストに入っています。"
        ),
        ephemeral=True,
    )


@bot.tree.command(name="blacklist_remove", description="ユーザーをブラックリストから削除します")
@app_commands.describe(user="解除するユーザー")
@app_commands.guild_only()
async def blacklist_remove(
    interaction: discord.Interaction,
    user: discord.Member,
) -> None:
    removed = remove_blacklist(interaction.user.id, user.id)
    await interaction.response.send_message(
        (
            f"✅ {user.mention} のブラックリスト登録を解除しました。"
            if removed
            else f"{user.mention} はブラックリストに登録されていません。"
        ),
        ephemeral=True,
    )


@bot.tree.command(name="blacklist_view", description="自分のブラックリストを確認します")
@app_commands.guild_only()
async def blacklist_view(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    ids = get_blacklist(interaction.user.id)
    names: list[str] = []

    if guild:
        for user_id in ids:
            member = guild.get_member(user_id)
            names.append(member.mention if member else f"不明なユーザー ({user_id})")

    text = "\n".join(f"・{name}" for name in names) or "登録されていません。"
    await interaction.response.send_message(
        f"🚫 **あなたのブラックリスト**\n{text}",
        ephemeral=True,
    )


@bot.tree.command(name="room_delete", description="自分が作成した部屋を削除します")
@app_commands.describe(channel="削除するボイスチャンネル")
@app_commands.guild_only()
async def room_delete(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
) -> None:
    row = get_room(channel.id)
    member = interaction.user

    if row is None:
        await interaction.response.send_message(
            "このBotで作成した部屋ではありません。",
            ephemeral=True,
        )
        return

    allowed = (
        int(row["owner_id"]) == interaction.user.id
        or (isinstance(member, discord.Member) and is_bot_admin(member))
    )
    if not allowed:
        await interaction.response.send_message(
            "作成者または管理者だけ削除できます。",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        await channel.delete(reason=f"{interaction.user} が部屋を削除")
        delete_room_record(channel.id)
        await interaction.followup.send("✅ 部屋を削除しました。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "Botにチャンネル管理権限がありません。",
            ephemeral=True,
        )


@bot.tree.command(name="room_invite", description="自分の作成部屋にユーザーを招待します")
@app_commands.describe(channel="招待先の部屋", user="招待するユーザー")
@app_commands.guild_only()
async def room_invite(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    user: discord.Member,
) -> None:
    row = get_room(channel.id)
    if row is None or int(row["owner_id"]) != interaction.user.id:
        await interaction.response.send_message(
            "自分が作成した部屋だけ操作できます。",
            ephemeral=True,
        )
        return

    if is_blocked_either_way(interaction.user.id, user.id):
        await interaction.response.send_message(
            "ブラックリスト関係のユーザーは招待できません。",
            ephemeral=True,
        )
        return

    try:
        await channel.set_permissions(
            user,
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            reason=f"{interaction.user} が招待",
        )
        await interaction.response.send_message(
            f"✅ {user.mention} を {channel.mention} に招待しました。",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Botに権限管理の権限がありません。",
            ephemeral=True,
        )


@bot.tree.command(name="room_uninvite", description="自分の作成部屋からユーザーを解除します")
@app_commands.describe(channel="対象の部屋", user="解除するユーザー")
@app_commands.guild_only()
async def room_uninvite(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    user: discord.Member,
) -> None:
    row = get_room(channel.id)
    if row is None or int(row["owner_id"]) != interaction.user.id:
        await interaction.response.send_message(
            "自分が作成した部屋だけ操作できます。",
            ephemeral=True,
        )
        return

    try:
        if user.voice and user.voice.channel and user.voice.channel.id == channel.id:
            await user.move_to(None, reason="個室の招待解除")

        # 表個室なら閲覧は全員設定に戻り、接続不可になる
        await channel.set_permissions(
            user,
            overwrite=None,
            reason=f"{interaction.user} が招待解除",
        )
        await interaction.response.send_message(
            f"✅ {user.mention} の招待を解除しました。",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Botに権限管理またはメンバー移動権限がありません。",
            ephemeral=True,
        )



@bot.tree.command(
    name="setup_recruitment_panels",
    description="裏募集作成・確認パネルを指定チャンネルへ設置します",
)
@app_commands.guild_only()
async def setup_recruitment_panels(
    interaction: discord.Interaction,
) -> None:
    member = interaction.user
    guild = interaction.guild

    if (
        guild is None
        or not isinstance(member, discord.Member)
        or not is_bot_admin(member)
    ):
        await interaction.response.send_message(
            "管理者専用コマンドです。",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    create_channel = guild.get_channel(RECRUIT_CREATE_PANEL_CHANNEL_ID)
    check_channel = guild.get_channel(RECRUIT_CHECK_PANEL_CHANNEL_ID)

    if not isinstance(create_channel, discord.TextChannel):
        await interaction.followup.send(
            "裏募集作成パネルのチャンネルが見つかりません。",
            ephemeral=True,
        )
        return

    if not isinstance(check_channel, discord.TextChannel):
        await interaction.followup.send(
            "裏募集確認パネルのチャンネルが見つかりません。",
            ephemeral=True,
        )
        return

    create_embed = discord.Embed(
        title="🌙 フィルタリング付き裏募集",
        description=(
            "**異性にのみ閲覧できる募集です。**\n\n"
            "1. プロフィール設定を行います。\n"
            "2. 裏募集を作成し、募集文章とフィルターを入力します。\n"
            "3. 匿名／記名、異性全体／マイリスト限定を選択します。\n"
            "4. 公開すると条件一致者だけにDM通知されます。\n\n"
            "募集は1人1件までです。新しく作る場合は以前の募集を削除してください。"
        ),
        color=discord.Color.dark_purple(),
    )
    await create_channel.send(
        embed=create_embed,
        view=RecruitCreatePanel(),
    )

    check_embed = discord.Embed(
        title="🌙 裏募集確認",
        description=(
            "【裏募集確認】を押すと、あなたの性別・プロフィール・"
            "マイリスト・ブラックリスト条件に合う募集だけが表示されます。\n\n"
            "条件に合わない募集は確認できません。"
        ),
        color=discord.Color.dark_purple(),
    )
    await check_channel.send(
        embed=check_embed,
        view=RecruitCheckPanel(),
    )

    await interaction.followup.send(
        "✅ 裏募集作成パネルと確認パネルを設置しました。",
        ephemeral=True,
    )


@bot.tree.command(
    name="recruit_profile",
    description="裏募集用プロフィールを文字入力で設定します",
)
@app_commands.describe(
    age="例：20代後半,30代",
    voice="例：低音,中音",
    personality="例：まったり,積極的",
    attribute="例：甘えたい,甘えられたい",
    playstyle="例：雑談中心,添い寝",
)
@app_commands.guild_only()
async def recruit_profile(
    interaction: discord.Interaction,
    age: str = "",
    voice: str = "",
    personality: str = "",
    attribute: str = "",
    playstyle: str = "",
) -> None:
    save_recruit_profile(
        interaction.user.id,
        age,
        voice,
        personality,
        attribute,
        playstyle,
    )
    await interaction.response.send_message(
        "✅ 裏募集プロフィールを保存しました。",
        ephemeral=True,
    )



# =========================================================
# 起動
# =========================================================

if __name__ == "__main__":
    if not TOKEN or TOKEN == "ここにBOTトークン":
        raise RuntimeError(
            "TOKENを設定してください。環境変数 DISCORD_TOKEN またはコード上部のTOKENを使用できます。"
        )

    bot.run(TOKEN)
