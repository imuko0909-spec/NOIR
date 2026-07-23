
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

# 裏募集
RECRUIT_CREATE_PANEL_CHANNEL_ID = 1524090558518132907
RECRUIT_CONFIRM_CHANNEL_ID = 1529514190497386606
RECRUIT_NOTIFICATION_CHANNEL_ID = 1521066957103693965
RECRUIT_LOG_CHANNEL_ID = 1529623100986097764
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
                timer_started INTEGER NOT NULL DEFAULT 0
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
            """
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
            connect=False,
        ),
        owner: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
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
        )

    if spec.opposite_gender_only:
        male = guild.get_role(MALE_ROLE_ID)
        female = guild.get_role(FEMALE_ROLE_ID)
        if male and female:
            if male in owner.roles:
                overwrites[female] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=("knock" not in spec.room_type),
                )
                overwrites[male] = discord.PermissionOverwrite(view_channel=False, connect=False)
            elif female in owner.roles:
                overwrites[male] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=("knock" not in spec.room_type),
                )
                overwrites[female] = discord.PermissionOverwrite(view_channel=False, connect=False)

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
        label="ノック",
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

        owner_id = int(row["owner_id"])
        if interaction.user.id == owner_id:
            await interaction.response.send_message(
                "作成者本人はノック不要です。",
                ephemeral=True,
            )
            return

        if is_blocked_either_way(owner_id, interaction.user.id):
            await interaction.response.send_message(
                "ブラックリスト関係のためノックできません。",
                ephemeral=True,
            )
            return

        owner = channel.guild.get_member(owner_id)
        await channel.send(
            content=(
                f"{owner.mention if owner else f'<@{owner_id}>'}\n"
                f"🚪 {interaction.user.mention} さんがノックしました！"
            ),
            view=KnockDecisionView(channel.id, interaction.user.id),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await interaction.response.send_message(
            "🚪 ノックを送りました。入室許可をお待ちください。",
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


class PrivateUserSelect(discord.ui.UserSelect):
    def __init__(self, hidden: bool):
        self.hidden = hidden
        super().__init__(
            placeholder="招待するユーザーを選択",
            min_values=1,
            max_values=10,
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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        row = get_application(self.application_id)
        if row is None:
            await interaction.response.send_message("応募情報が見つかりません。", ephemeral=True)
            return False
        if interaction.user.id != int(row["owner_id"]):
            await interaction.response.send_message("募集主だけ操作できます。", ephemeral=True)
            return False
        return True

    async def approve(self, interaction: discord.Interaction) -> None:
        row = get_application(self.application_id)
        if row is None or row["status"] != "pending":
            await interaction.response.send_message("この応募は処理済みです。", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            return

        owner = guild.get_member(int(row["owner_id"]))
        applicant = guild.get_member(int(row["applicant_id"]))
        if owner is None or applicant is None:
            await interaction.response.send_message("ユーザーが見つかりません。", ephemeral=True)
            return

        update_application(self.application_id, "approved")

        parent = get_text_channel(guild, RECRUIT_NOTIFICATION_CHANNEL_ID)
        thread_text = ""
        if parent:
            try:
                thread = await parent.create_thread(
                    name=clean_channel_name(f"連絡用｜{owner.display_name}×{applicant.display_name}"),
                    type=discord.ChannelType.private_thread,
                    reason="募集応募が承認されたため",
                )
                await thread.add_user(owner)
                await thread.add_user(applicant)
                await thread.send(
                    f"{owner.mention} {applicant.mention}\n"
                    "応募が承認されました。このスレッドで連絡してください。"
                )
                thread_text = f"\n連絡用スレッド：{thread.mention}"
            except Exception:
                log.exception("連絡用スレッド作成失敗")

        try:
            await applicant.send(f"✅ {guild.name} の募集への応募が承認されました。{thread_text}")
        except discord.HTTPException:
            pass

        await send_log(guild, f"✅ 応募承認：募集主 {owner.mention} / 応募者 {applicant.mention}")
        await interaction.response.edit_message(
            content=(interaction.message.content or "") + f"\n\n✅ 承認済み：{applicant.mention}{thread_text}",
            view=None,
        )

    async def reject(self, interaction: discord.Interaction) -> None:
        row = get_application(self.application_id)
        if row is None or row["status"] != "pending":
            await interaction.response.send_message("この応募は処理済みです。", ephemeral=True)
            return

        update_application(self.application_id, "rejected")
        guild = interaction.guild
        if guild:
            applicant = guild.get_member(int(row["applicant_id"]))
            if applicant:
                try:
                    await applicant.send(f"今回は募集への応募が見送られました。")
                except discord.HTTPException:
                    pass
            await send_log(guild, f"❌ 応募却下：application={self.application_id}")

        await interaction.response.edit_message(
            content=(interaction.message.content or "") + "\n\n❌ お断り済み",
            view=None,
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
                await owner.send(
                    f"💞 {interaction.guild.name} で立候補が届きました。\n"
                    f"応募者：{interaction.user} ({interaction.user.id})\n"
                    f"募集：{interaction.message.jump_url}"
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
# Bot
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True


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
        self.add_view(VCMenuView())
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
