from __future__ import annotations

import os
import sqlite3
import time
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# 基本設定
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "環境変数 DISCORD_TOKEN が設定されていません。\n"
        "Render等のEnvironmentに DISCORD_TOKEN を設定してください。"
    )

DATABASE_PATH = "kokuhaku.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("kokuhaku_bot")


# =========================================================
# Database
# =========================================================

db = sqlite3.connect(DATABASE_PATH)
db.row_factory = sqlite3.Row


def init_database():
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            panel_channel_id INTEGER,
            log_channel_id INTEGER,
            male_role_id INTEGER,
            female_role_id INTEGER,
            target_rule TEXT NOT NULL DEFAULT 'all',
            cooldown_seconds INTEGER NOT NULL DEFAULT 180
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS confessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            responded_at INTEGER,
            dm_message_id INTEGER
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cooldowns (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_sent_at INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )

    db.commit()


def get_settings(guild_id: int):
    return db.execute(
        """
        SELECT *
        FROM guild_settings
        WHERE guild_id = ?
        """,
        (guild_id,)
    ).fetchone()


def save_settings(
    guild_id: int,
    panel_channel_id: int,
    log_channel_id: Optional[int],
    male_role_id: int,
    female_role_id: int,
    target_rule: str,
    cooldown_seconds: int
):
    db.execute(
        """
        INSERT INTO guild_settings (
            guild_id,
            panel_channel_id,
            log_channel_id,
            male_role_id,
            female_role_id,
            target_rule,
            cooldown_seconds
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            panel_channel_id = excluded.panel_channel_id,
            log_channel_id = excluded.log_channel_id,
            male_role_id = excluded.male_role_id,
            female_role_id = excluded.female_role_id,
            target_rule = excluded.target_rule,
            cooldown_seconds = excluded.cooldown_seconds
        """,
        (
            guild_id,
            panel_channel_id,
            log_channel_id,
            male_role_id,
            female_role_id,
            target_rule,
            cooldown_seconds
        )
    )

    db.commit()


def get_cooldown(guild_id: int, user_id: int) -> Optional[int]:
    row = db.execute(
        """
        SELECT last_sent_at
        FROM cooldowns
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id)
    ).fetchone()

    if row:
        return row["last_sent_at"]

    return None


def set_cooldown(guild_id: int, user_id: int):
    now = int(time.time())

    db.execute(
        """
        INSERT INTO cooldowns (
            guild_id,
            user_id,
            last_sent_at
        )
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            last_sent_at = excluded.last_sent_at
        """,
        (guild_id, user_id, now)
    )

    db.commit()


def create_confession(
    guild_id: int,
    sender_id: int,
    target_id: int,
    message: str
) -> int:
    cursor = db.execute(
        """
        INSERT INTO confessions (
            guild_id,
            sender_id,
            target_id,
            message,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (
            guild_id,
            sender_id,
            target_id,
            message,
            int(time.time())
        )
    )

    db.commit()
    return cursor.lastrowid


def get_confession(confession_id: int):
    return db.execute(
        """
        SELECT *
        FROM confessions
        WHERE id = ?
        """,
        (confession_id,)
    ).fetchone()


def update_confession_message_id(
    confession_id: int,
    message_id: int
):
    db.execute(
        """
        UPDATE confessions
        SET dm_message_id = ?
        WHERE id = ?
        """,
        (message_id, confession_id)
    )

    db.commit()


def finish_confession(
    confession_id: int,
    status: str
):
    db.execute(
        """
        UPDATE confessions
        SET
            status = ?,
            responded_at = ?
        WHERE id = ?
        """,
        (
            status,
            int(time.time()),
            confession_id
        )
    )

    db.commit()


# =========================================================
# 補助関数
# =========================================================

def get_member_gender(
    member: discord.Member,
    male_role_id: int,
    female_role_id: int
) -> Optional[str]:

    role_ids = {role.id for role in member.roles}

    has_male = male_role_id in role_ids
    has_female = female_role_id in role_ids

    if has_male and not has_female:
        return "male"

    if has_female and not has_male:
        return "female"

    return None


def target_rule_text(rule: str) -> str:
    if rule == "opposite":
        return "異性のみ"

    if rule == "same":
        return "同性のみ"

    return "制限なし"


async def send_log(
    bot: commands.Bot,
    guild_id: int,
    text: str
):
    settings = get_settings(guild_id)

    if not settings:
        return

    channel_id = settings["log_channel_id"]

    if not channel_id:
        return

    channel = bot.get_channel(channel_id)

    if not channel:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return

    try:
        await channel.send(text)
    except Exception:
        pass


# =========================================================
# 承認 / 拒否
# =========================================================

class ConfessionResponseView(discord.ui.View):

    def __init__(self, confession_id: int):
        super().__init__(timeout=None)

        self.confession_id = confession_id

        accept_button = discord.ui.Button(
            label="承認する",
            emoji="❤️",
            style=discord.ButtonStyle.success,
            custom_id=f"kokuhaku_accept:{confession_id}"
        )

        reject_button = discord.ui.Button(
            label="お断りする",
            emoji="💔",
            style=discord.ButtonStyle.danger,
            custom_id=f"kokuhaku_reject:{confession_id}"
        )

        accept_button.callback = self.accept_callback
        reject_button.callback = self.reject_callback

        self.add_item(accept_button)
        self.add_item(reject_button)

    async def accept_callback(
        self,
        interaction: discord.Interaction
    ):
        await self.process_response(
            interaction,
            "accepted"
        )

    async def reject_callback(
        self,
        interaction: discord.Interaction
    ):
        await self.process_response(
            interaction,
            "rejected"
        )

    async def process_response(
        self,
        interaction: discord.Interaction,
        result: str
    ):
        confession = get_confession(self.confession_id)

        if not confession:
            await interaction.response.send_message(
                "この告白データが見つかりません。",
                ephemeral=True
            )
            return

        if confession["status"] != "pending":
            await interaction.response.send_message(
                "この告白にはすでに回答済みです。",
                ephemeral=True
            )
            return

        if interaction.user.id != confession["target_id"]:
            await interaction.response.send_message(
                "この告白に回答できるのは受信者本人だけです。",
                ephemeral=True
            )
            return

        finish_confession(
            self.confession_id,
            result
        )

        # ボタン無効化
        for item in self.children:
            item.disabled = True

        if result == "accepted":
            title = "❤️ 告白を承認しました"
            description = (
                "告白を承認しました。\n\n"
                "告白した相手にもBotから結果を通知しました。"
            )
            color = discord.Color.green()
        else:
            title = "💔 告白をお断りしました"
            description = (
                "告白をお断りしました。\n\n"
                "告白した相手にはBotから結果のみ通知されます。"
            )
            color = discord.Color.red()

        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )

        try:
            await interaction.response.edit_message(
                embed=embed,
                view=self
            )
        except discord.InteractionResponded:
            pass

        # 告白した側へDM
        try:
            sender = interaction.client.get_user(
                confession["sender_id"]
            )

            if not sender:
                sender = await interaction.client.fetch_user(
                    confession["sender_id"]
                )

            if result == "accepted":
                result_embed = discord.Embed(
                    title="💞 告白が承認されました！",
                    description=(
                        "あなたが送った告白が承認されました。\n\n"
                        "相手もあなたの気持ちを受け取ってくれました。"
                    ),
                    color=discord.Color.green()
                )

            else:
                result_embed = discord.Embed(
                    title="💔 告白の結果",
                    description=(
                        "あなたが送った告白は、"
                        "今回はお断りとなりました。\n\n"
                        "相手への追及や連続した告白は控えてください。"
                    ),
                    color=discord.Color.red()
                )

            await sender.send(embed=result_embed)

        except discord.Forbidden:
            pass
        except Exception:
            logger.exception("告白結果DM送信エラー")

        # 管理ログ
        if result == "accepted":
            status_text = "承認"
        else:
            status_text = "拒否"

        await send_log(
            interaction.client,
            confession["guild_id"],
            (
                f"💌 告白結果\n"
                f"送信者: <@{confession['sender_id']}> "
                f"(`{confession['sender_id']}`)\n"
                f"受信者: <@{confession['target_id']}> "
                f"(`{confession['target_id']}`)\n"
                f"結果: **{status_text}**"
            )
        )


# =========================================================
# 告白本文入力
# =========================================================

class ConfessionModal(
    discord.ui.Modal,
    title="💌 告白メッセージ"
):

    message = discord.ui.TextInput(
        label="告白メッセージ",
        placeholder="相手に伝えたい気持ちを書いてください",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000,
        required=True
    )

    def __init__(
        self,
        target: discord.Member
    ):
        super().__init__()

        self.target = target

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True
            )
            return

        guild = interaction.guild
        sender = interaction.user

        settings = get_settings(guild.id)

        if not settings:
            await interaction.response.send_message(
                "このサーバーでは告白Botの設定が完了していません。",
                ephemeral=True
            )
            return

        # 再度クールタイム確認
        last_sent = get_cooldown(
            guild.id,
            sender.id
        )

        cooldown_seconds = settings["cooldown_seconds"]

        if last_sent:
            elapsed = int(time.time()) - last_sent

            if elapsed < cooldown_seconds:
                remain = cooldown_seconds - elapsed

                minutes = remain // 60
                seconds = remain % 60

                await interaction.response.send_message(
                    (
                        "⏱️ まだ次の告白は送れません。\n"
                        f"あと **{minutes}分{seconds}秒** "
                        "お待ちください。"
                    ),
                    ephemeral=True
                )
                return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True
        )

        confession_id = create_confession(
            guild.id,
            sender.id,
            self.target.id,
            str(self.message.value)
        )

        embed = discord.Embed(
            title="💌 告白が届きました",
            description=(
                f"**{sender.display_name}** さんから"
                "告白が届きました。\n\n"
                "━━━━━━━━━━━━━━\n\n"
                f"{self.message.value}\n\n"
                "━━━━━━━━━━━━━━\n\n"
                "下のボタンから返答してください。"
            ),
            color=discord.Color.from_rgb(
                255,
                105,
                180
            )
        )

        embed.set_footer(
            text="返答するとBotから相手へ結果が通知されます"
        )

        view = ConfessionResponseView(
            confession_id
        )

        try:
            dm_message = await self.target.send(
                embed=embed,
                view=view
            )

            update_confession_message_id(
                confession_id,
                dm_message.id
            )

        except discord.Forbidden:
            finish_confession(
                confession_id,
                "dm_failed"
            )

            await interaction.followup.send(
                (
                    "❌ 相手にDMを送信できませんでした。\n\n"
                    "相手がBotからのDMを受信できない設定に"
                    "している可能性があります。\n"
                    "この告白は送信扱いにはなりません。"
                ),
                ephemeral=True
            )
            return

        except Exception:
            finish_confession(
                confession_id,
                "dm_failed"
            )

            logger.exception(
                "告白DM送信エラー"
            )

            await interaction.followup.send(
                "❌ 告白の送信中にエラーが発生しました。",
                ephemeral=True
            )
            return

        set_cooldown(
            guild.id,
            sender.id
        )

        await interaction.followup.send(
            (
                f"💌 **{self.target.display_name}** さんへ"
                "告白を送りました。\n\n"
                "相手が「承認する」または"
                "「お断りする」を選ぶと、"
                "Botからあなたへ結果が届きます。"
            ),
            ephemeral=True
        )

        # 管理ログ
        # 告白内容そのものは記録しない
        await send_log(
            interaction.client,
            guild.id,
            (
                f"💌 告白送信\n"
                f"送信者: <@{sender.id}> (`{sender.id}`)\n"
                f"受信者: <@{self.target.id}> "
                f"(`{self.target.id}`)\n"
                f"告白ID: `{confession_id}`"
            )
        )


# =========================================================
# 相手選択
# =========================================================

class TargetSelect(discord.ui.UserSelect):

    def __init__(self):
        super().__init__(
            placeholder="告白する相手を選んでください",
            min_values=1,
            max_values=1
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):
        if not interaction.guild:
            return

        selected = self.values[0]

        if selected.id == interaction.user.id:
            await interaction.response.send_message(
                "自分自身には告白できません。",
                ephemeral=True
            )
            return

        if selected.bot:
            await interaction.response.send_message(
                "Botには告白できません。",
                ephemeral=True
            )
            return

        guild = interaction.guild

        # Member型を取得
        if isinstance(selected, discord.Member):
            target = selected
        else:
            try:
                target = await guild.fetch_member(
                    selected.id
                )
            except discord.NotFound:
                await interaction.response.send_message(
                    "そのユーザーをサーバー内で確認できませんでした。",
                    ephemeral=True
                )
                return

        if isinstance(interaction.user, discord.Member):
            sender = interaction.user
        else:
            try:
                sender = await guild.fetch_member(
                    interaction.user.id
                )
            except Exception:
                await interaction.response.send_message(
                    "あなたのメンバー情報を取得できませんでした。",
                    ephemeral=True
                )
                return

        settings = get_settings(
            guild.id
        )

        if not settings:
            await interaction.response.send_message(
                "告白Botの設定が完了していません。",
                ephemeral=True
            )
            return

        rule = settings["target_rule"]

        # 制限なしなら性別ロール判定不要
        if rule != "all":
            sender_gender = get_member_gender(
                sender,
                settings["male_role_id"],
                settings["female_role_id"]
            )

            target_gender = get_member_gender(
                target,
                settings["male_role_id"],
                settings["female_role_id"]
            )

            if not sender_gender:
                await interaction.response.send_message(
                    (
                        "あなたの性別ロールを確認できませんでした。\n"
                        "男性または女性ロールが必要です。"
                    ),
                    ephemeral=True
                )
                return

            if not target_gender:
                await interaction.response.send_message(
                    (
                        "相手の性別ロールを確認できませんでした。\n"
                        "別の相手を選択してください。"
                    ),
                    ephemeral=True
                )
                return

            if (
                rule == "opposite"
                and sender_gender == target_gender
            ):
                await interaction.response.send_message(
                    "このサーバーでは異性への告白のみ可能です。",
                    ephemeral=True
                )
                return

            if (
                rule == "same"
                and sender_gender != target_gender
            ):
                await interaction.response.send_message(
                    "このサーバーでは同性への告白のみ可能です。",
                    ephemeral=True
                )
                return

        await interaction.response.send_modal(
            ConfessionModal(
                target=target
            )
        )


class TargetSelectView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=120
        )

        self.add_item(
            TargetSelect()
        )


# =========================================================
# 告白パネル
# =========================================================

class ConfessionPanelView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="告白する",
        emoji="💌",
        style=discord.ButtonStyle.success,
        custom_id="kokuhaku_main_button"
    )
    async def confession_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True
            )
            return

        settings = get_settings(
            interaction.guild.id
        )

        if not settings:
            await interaction.response.send_message(
                (
                    "このサーバーではまだ設定されていません。\n"
                    "管理者にお問い合わせください。"
                ),
                ephemeral=True
            )
            return

        # クールタイム
        last_sent = get_cooldown(
            interaction.guild.id,
            interaction.user.id
        )

        cooldown = settings["cooldown_seconds"]

        if last_sent:
            elapsed = int(time.time()) - last_sent

            if elapsed < cooldown:
                remain = cooldown - elapsed

                minutes = remain // 60
                seconds = remain % 60

                await interaction.response.send_message(
                    (
                        "⏱️ 次の告白まで\n"
                        f"**あと {minutes}分{seconds}秒** "
                        "お待ちください。"
                    ),
                    ephemeral=True
                )
                return

        embed = discord.Embed(
            title="💌 告白する相手を選択",
            description=(
                "下のメニューから告白したい相手を"
                "1人選んでください。\n\n"
                "この画面は**あなたにだけ表示されています。**\n"
                "選択後、告白メッセージを入力できます。"
            ),
            color=discord.Color.from_rgb(
                255,
                105,
                180
            )
        )

        await interaction.response.send_message(
            embed=embed,
            view=TargetSelectView(),
            ephemeral=True
        )


# =========================================================
# Bot
# =========================================================

class KokuhakuBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):
        init_database()

        # パネルボタンを再起動後も有効化
        self.add_view(
            ConfessionPanelView()
        )

        # 未回答の告白ボタンを再登録
        pending = db.execute(
            """
            SELECT id
            FROM confessions
            WHERE status = 'pending'
            """
        ).fetchall()

        for row in pending:
            self.add_view(
                ConfessionResponseView(
                    row["id"]
                )
            )

        try:
            synced = await self.tree.sync()

            logger.info(
                "%s個のコマンドを同期しました。",
                len(synced)
            )

        except Exception:
            logger.exception(
                "コマンド同期エラー"
            )

    async def on_ready(self):
        logger.info(
            "ログインしました: %s (%s)",
            self.user,
            self.user.id
        )


bot = KokuhakuBot()


# =========================================================
# /告白設定
# =========================================================

@bot.tree.command(
    name="告白設定",
    description="匿名告白Botの設定を行います"
)
@app_commands.guild_only()
@app_commands.default_permissions(
    manage_guild=True
)
@app_commands.describe(
    パネルチャンネル="告白パネルを設置するチャンネル",
    男性ロール="男性用ロール",
    女性ロール="女性用ロール",
    告白対象="誰に告白できるか",
    クールタイム="告白後の待ち時間（分）",
    管理ログ="管理ログを送るチャンネル（任意）"
)
@app_commands.choices(
    告白対象=[
        app_commands.Choice(
            name="制限なし",
            value="all"
        ),
        app_commands.Choice(
            name="異性のみ",
            value="opposite"
        ),
        app_commands.Choice(
            name="同性のみ",
            value="same"
        )
    ]
)
async def kokuhaku_setup(
    interaction: discord.Interaction,
    パネルチャンネル: discord.TextChannel,
    男性ロール: discord.Role,
    女性ロール: discord.Role,
    告白対象: app_commands.Choice[str],
    クールタイム: app_commands.Range[int, 0, 1440] = 3,
    管理ログ: Optional[discord.TextChannel] = None
):
    if not interaction.guild:
        return

    save_settings(
        guild_id=interaction.guild.id,
        panel_channel_id=パネルチャンネル.id,
        log_channel_id=(
            管理ログ.id
            if 管理ログ
            else None
        ),
        male_role_id=男性ロール.id,
        female_role_id=女性ロール.id,
        target_rule=告白対象.value,
        cooldown_seconds=クールタイム * 60
    )

    embed = discord.Embed(
        title="✅ 告白Bot設定完了",
        color=discord.Color.green()
    )

    embed.add_field(
        name="💌 パネル",
        value=パネルチャンネル.mention,
        inline=False
    )

    embed.add_field(
        name="♂ 男性ロール",
        value=男性ロール.mention,
        inline=True
    )

    embed.add_field(
        name="♀ 女性ロール",
        value=女性ロール.mention,
        inline=True
    )

    embed.add_field(
        name="💞 告白対象",
        value=target_rule_text(
            告白対象.value
        ),
        inline=False
    )

    embed.add_field(
        name="⏱️ クールタイム",
        value=f"{クールタイム}分",
        inline=True
    )

    embed.add_field(
        name="📝 管理ログ",
        value=(
            管理ログ.mention
            if 管理ログ
            else "使用しない"
        ),
        inline=True
    )

    embed.set_footer(
        text="続けて /告白パネル を実行してください"
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# /告白パネル
# =========================================================

@bot.tree.command(
    name="告白パネル",
    description="設定したチャンネルに告白パネルを設置します"
)
@app_commands.guild_only()
@app_commands.default_permissions(
    manage_guild=True
)
async def kokuhaku_panel(
    interaction: discord.Interaction
):
    if not interaction.guild:
        return

    settings = get_settings(
        interaction.guild.id
    )

    if not settings:
        await interaction.response.send_message(
            (
                "❌ 先に `/告白設定` を"
                "実行してください。"
            ),
            ephemeral=True
        )
        return

    channel = interaction.guild.get_channel(
        settings["panel_channel_id"]
    )

    if not channel:
        await interaction.response.send_message(
            (
                "設定されているパネルチャンネルが"
                "見つかりません。\n"
                "`/告白設定` をやり直してください。"
            ),
            ephemeral=True
        )
        return

    cooldown_minutes = (
        settings["cooldown_seconds"] // 60
    )

    embed = discord.Embed(
        title="💌 匿名告白",
        description=(
            "気になる相手へ、"
            "Botを通して気持ちを届けられます。\n\n"
            "告白内容はサーバーには公開されません。\n"
            "選択した相手にのみBotから通知されます。\n\n"
            "相手には**あなたの名前が表示されます。**\n\n"
            "相手が\n"
            "❤️ **承認する**\n"
            "💔 **お断りする**\n"
            "のどちらかを選択すると、"
            "結果がBotからあなたへ届きます。\n\n"
            f"⏱️ 送信後の待ち時間："
            f"**{cooldown_minutes}分**\n"
            f"💞 告白対象："
            f"**{target_rule_text(settings['target_rule'])}**\n\n"
            "下の「💌 告白する」から開始してください。"
        ),
        color=discord.Color.from_rgb(
            255,
            105,
            180
        )
    )

    try:
        await channel.send(
            embed=embed,
            view=ConfessionPanelView()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            (
                "❌ パネルチャンネルに"
                "メッセージを送信する権限がありません。"
            ),
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ {channel.mention} に告白パネルを設置しました。",
        ephemeral=True
    )


# =========================================================
# /告白設定確認
# =========================================================

@bot.tree.command(
    name="告白設定確認",
    description="現在の匿名告白Bot設定を確認します"
)
@app_commands.guild_only()
@app_commands.default_permissions(
    manage_guild=True
)
async def kokuhaku_settings(
    interaction: discord.Interaction
):
    if not interaction.guild:
        return

    settings = get_settings(
        interaction.guild.id
    )

    if not settings:
        await interaction.response.send_message(
            "まだ設定されていません。",
            ephemeral=True
        )
        return

    panel = interaction.guild.get_channel(
        settings["panel_channel_id"]
    )

    log_channel = (
        interaction.guild.get_channel(
            settings["log_channel_id"]
        )
        if settings["log_channel_id"]
        else None
    )

    male_role = interaction.guild.get_role(
        settings["male_role_id"]
    )

    female_role = interaction.guild.get_role(
        settings["female_role_id"]
    )

    embed = discord.Embed(
        title="⚙️ 匿名告白Bot 設定",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="💌 パネル",
        value=(
            panel.mention
            if panel
            else "不明"
        ),
        inline=False
    )

    embed.add_field(
        name="♂ 男性ロール",
        value=(
            male_role.mention
            if male_role
            else "不明"
        ),
        inline=True
    )

    embed.add_field(
        name="♀ 女性ロール",
        value=(
            female_role.mention
            if female_role
            else "不明"
        ),
        inline=True
    )

    embed.add_field(
        name="💞 告白対象",
        value=target_rule_text(
            settings["target_rule"]
        ),
        inline=False
    )

    embed.add_field(
        name="⏱️ クールタイム",
        value=(
            f"{settings['cooldown_seconds'] // 60}分"
        ),
        inline=True
    )

    embed.add_field(
        name="📝 管理ログ",
        value=(
            log_channel.mention
            if log_channel
            else "使用しない"
        ),
        inline=True
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# エラーハンドラー
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    logger.exception(
        "Application command error",
        exc_info=error
    )

    message = (
        "❌ コマンド実行中にエラーが発生しました。"
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )
    except Exception:
        pass


# =========================================================
# 起動
# =========================================================

bot.run(TOKEN)
