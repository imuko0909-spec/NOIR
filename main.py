from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# 💌 匿名告白Bot
# マルチサーバー対応
#
# ・サーバーごとに設定可能
# ・男女どちらからでも告白可能
# ・異性のみ / 同性のみ / 制限なし
# ・告白相手へBotからDM
# ・承認 / お断り
# ・結果をBot経由で送信者へ通知
# ・DM申請なし
# ・専用個室なし
# ・3秒タイムアウト
# ・SQLite保存
# =========================================================


# =========================================================
# 基本設定
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

if not TOKEN:
    raise RuntimeError(
        "環境変数 DISCORD_TOKEN が設定されていません。\n"
        "Render等のEnvironmentに DISCORD_TOKEN を設定してください。"
    )


DATABASE_PATH = "kokuhaku.db"

# DiscordへのDM送信が何秒以上かかったらタイムアウトにするか
DM_TIMEOUT_SECONDS = 3.0


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("kokuhaku_bot")


# =========================================================
# SQLite
# =========================================================

db = sqlite3.connect(
    DATABASE_PATH,
    check_same_thread=False
)

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


# =========================================================
# 設定取得
# =========================================================

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


# =========================================================
# クールタイム
# =========================================================

def get_cooldown(
    guild_id: int,
    user_id: int
) -> Optional[int]:

    row = db.execute(
        """
        SELECT last_sent_at
        FROM cooldowns
        WHERE guild_id = ?
        AND user_id = ?
        """,
        (
            guild_id,
            user_id
        )
    ).fetchone()

    if row:
        return row["last_sent_at"]

    return None


def set_cooldown(
    guild_id: int,
    user_id: int
):

    now = int(time.time())

    db.execute(
        """
        INSERT INTO cooldowns (
            guild_id,
            user_id,
            last_sent_at
        )
        VALUES (?, ?, ?)

        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET
            last_sent_at = excluded.last_sent_at
        """,
        (
            guild_id,
            user_id,
            now
        )
    )

    db.commit()


# =========================================================
# 告白データ
# =========================================================

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


def get_confession(
    confession_id: int
):

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
        (
            message_id,
            confession_id
        )
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
# 性別判定
# =========================================================

def get_member_gender(
    member: discord.Member,
    male_role_id: int,
    female_role_id: int
) -> Optional[str]:

    role_ids = {
        role.id
        for role in member.roles
    }

    has_male = male_role_id in role_ids
    has_female = female_role_id in role_ids

    if has_male and not has_female:
        return "male"

    if has_female and not has_male:
        return "female"

    return None


# =========================================================
# 表示用
# =========================================================

def target_rule_text(
    rule: str
) -> str:

    if rule == "opposite":
        return "異性のみ"

    if rule == "same":
        return "同性のみ"

    return "制限なし"


# =========================================================
# 管理ログ
# =========================================================

async def send_log(
    bot: commands.Bot,
    guild_id: int,
    text: str
):

    settings = get_settings(
        guild_id
    )

    if not settings:
        return

    channel_id = settings[
        "log_channel_id"
    ]

    if not channel_id:
        return

    channel = bot.get_channel(
        channel_id
    )

    if not channel:
        return

    try:

        await asyncio.wait_for(
            channel.send(text),
            timeout=DM_TIMEOUT_SECONDS
        )

    except asyncio.TimeoutError:

        logger.warning(
            "管理ログ送信タイムアウト"
        )

    except Exception:

        logger.exception(
            "管理ログ送信エラー"
        )


# =========================================================
# 告白 承認 / 拒否
# =========================================================

class ConfessionResponseView(
    discord.ui.View
):

    def __init__(
        self,
        confession_id: int
    ):

        super().__init__(
            timeout=None
        )

        self.confession_id = (
            confession_id
        )

        accept_button = discord.ui.Button(
            label="承認する",
            emoji="❤️",
            style=discord.ButtonStyle.success,
            custom_id=(
                f"kokuhaku_accept:"
                f"{confession_id}"
            )
        )

        reject_button = discord.ui.Button(
            label="お断りする",
            emoji="💔",
            style=discord.ButtonStyle.danger,
            custom_id=(
                f"kokuhaku_reject:"
                f"{confession_id}"
            )
        )

        accept_button.callback = (
            self.accept_callback
        )

        reject_button.callback = (
            self.reject_callback
        )

        self.add_item(
            accept_button
        )

        self.add_item(
            reject_button
        )


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

        confession = get_confession(
            self.confession_id
        )

        if not confession:

            await interaction.response.send_message(
                "❌ この告白データが見つかりません。",
                ephemeral=True
            )

            return


        if confession["status"] != "pending":

            await interaction.response.send_message(
                "この告白にはすでに回答済みです。",
                ephemeral=True
            )

            return


        if (
            interaction.user.id
            != confession["target_id"]
        ):

            await interaction.response.send_message(
                "この告白に回答できるのは受信者本人だけです。",
                ephemeral=True
            )

            return


        # -----------------------------------------
        # Discordへ即座に応答
        # ボタンの「考え中」を防ぐ
        # -----------------------------------------

        await interaction.response.defer()


        finish_confession(
            self.confession_id,
            result
        )


        # ボタン無効化
        for item in self.children:
            item.disabled = True


        if result == "accepted":

            embed = discord.Embed(
                title="❤️ 告白を承認しました",
                description=(
                    "告白を承認しました。\n\n"
                    "告白した相手へBotから"
                    "結果を通知します。"
                ),
                color=discord.Color.green()
            )

        else:

            embed = discord.Embed(
                title="💔 告白をお断りしました",
                description=(
                    "告白をお断りしました。\n\n"
                    "告白した相手には"
                    "Botから結果のみ通知されます。"
                ),
                color=discord.Color.red()
            )


        try:

            await interaction.edit_original_response(
                embed=embed,
                view=self
            )

        except Exception:

            logger.exception(
                "回答メッセージ更新エラー"
            )


        # -----------------------------------------
        # 告白した側へ結果DM
        # -----------------------------------------

        sender = interaction.client.get_user(
            confession["sender_id"]
        )

        if not sender:

            try:

                sender = await asyncio.wait_for(
                    interaction.client.fetch_user(
                        confession["sender_id"]
                    ),
                    timeout=DM_TIMEOUT_SECONDS
                )

            except Exception:

                sender = None


        if sender:

            if result == "accepted":

                result_embed = discord.Embed(
                    title="💞 告白が承認されました！",
                    description=(
                        "あなたが送った告白が"
                        "承認されました。\n\n"
                        "相手があなたの気持ちを"
                        "受け取ってくれました。"
                    ),
                    color=discord.Color.green()
                )

            else:

                result_embed = discord.Embed(
                    title="💔 告白の結果",
                    description=(
                        "あなたが送った告白は、"
                        "今回はお断りとなりました。\n\n"
                        "相手への追及や"
                        "連続した告白は控えてください。"
                    ),
                    color=discord.Color.red()
                )


            try:

                await asyncio.wait_for(
                    sender.send(
                        embed=result_embed
                    ),
                    timeout=DM_TIMEOUT_SECONDS
                )

            except asyncio.TimeoutError:

                logger.warning(
                    "告白結果DMが3秒でタイムアウトしました"
                )

            except discord.Forbidden:

                logger.info(
                    "送信者のDMが閉じています"
                )

            except Exception:

                logger.exception(
                    "告白結果DM送信エラー"
                )


        # -----------------------------------------
        # 管理ログ
        # -----------------------------------------

        if result == "accepted":
            status_text = "承認"
        else:
            status_text = "拒否"


        asyncio.create_task(
            send_log(
                interaction.client,
                confession["guild_id"],
                (
                    "💌 **告白結果**\n"
                    f"送信者："
                    f"<@{confession['sender_id']}>\n"
                    f"受信者："
                    f"<@{confession['target_id']}>\n"
                    f"結果：**{status_text}**\n"
                    f"告白ID：`{self.confession_id}`"
                )
            )
        )


# =========================================================
# 告白メッセージ入力
# =========================================================

class ConfessionModal(
    discord.ui.Modal,
    title="💌 告白メッセージ"
):

    message = discord.ui.TextInput(
        label="告白メッセージ",
        placeholder=(
            "相手に伝えたい気持ちを書いてください"
        ),
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


        settings = get_settings(
            guild.id
        )


        if not settings:

            await interaction.response.send_message(
                "このサーバーでは告白Botの設定が完了していません。",
                ephemeral=True
            )

            return


        # -----------------------------------------
        # クールタイム確認
        # -----------------------------------------

        last_sent = get_cooldown(
            guild.id,
            sender.id
        )

        cooldown_seconds = (
            settings[
                "cooldown_seconds"
            ]
        )


        if last_sent:

            elapsed = (
                int(time.time())
                - last_sent
            )

            if elapsed < cooldown_seconds:

                remain = (
                    cooldown_seconds
                    - elapsed
                )

                minutes = remain // 60
                seconds = remain % 60

                await interaction.response.send_message(
                    (
                        "⏱️ まだ次の告白は送れません。\n\n"
                        f"あと **{minutes}分"
                        f"{seconds}秒** "
                        "お待ちください。"
                    ),
                    ephemeral=True
                )

                return


        # -----------------------------------------
        # まず即座に応答
        #
        # 「考え中...」にせず
        # すぐメッセージを表示する
        # -----------------------------------------

        await interaction.response.send_message(
            "💌 告白を送信しています…",
            ephemeral=True
        )


        confession_id = create_confession(
            guild.id,
            sender.id,
            self.target.id,
            str(self.message.value)
        )


        confession_embed = discord.Embed(
            title="💌 告白が届きました",
            description=(
                f"**{sender.display_name}** さんから"
                "告白が届きました。\n\n"
                "━━━━━━━━━━━━━━\n\n"
                f"{self.message.value}\n\n"
                "━━━━━━━━━━━━━━\n\n"
                "下のボタンから"
                "返答してください。"
            ),
            color=discord.Color.from_rgb(
                255,
                105,
                180
            )
        )


        confession_embed.set_footer(
            text=(
                "返答するとBotから"
                "相手へ結果が通知されます"
            )
        )


        view = ConfessionResponseView(
            confession_id
        )


        # -----------------------------------------
        # DM送信
        # 3秒超えたらタイムアウト
        # -----------------------------------------

        try:

            dm_message = await asyncio.wait_for(
                self.target.send(
                    embed=confession_embed,
                    view=view
                ),
                timeout=DM_TIMEOUT_SECONDS
            )


        except asyncio.TimeoutError:

            finish_confession(
                confession_id,
                "timeout"
            )

            await interaction.edit_original_response(
                content=(
                    "❌ 処理に時間がかかりすぎたため、"
                    "送信を中止しました。\n\n"
                    "もう一度お試しください。"
                )
            )

            return


        except discord.Forbidden:

            finish_confession(
                confession_id,
                "dm_failed"
            )

            await interaction.edit_original_response(
                content=(
                    "❌ 相手にDMを送信できませんでした。\n\n"
                    "相手がBotからのDMを"
                    "受信できない設定にしている"
                    "可能性があります。\n\n"
                    "この告白は送信扱いにはなりません。"
                )
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

            await interaction.edit_original_response(
                content=(
                    "❌ 告白の送信中に"
                    "エラーが発生しました。"
                )
            )

            return


        # -----------------------------------------
        # DM送信成功
        # -----------------------------------------

        update_confession_message_id(
            confession_id,
            dm_message.id
        )


        set_cooldown(
            guild.id,
            sender.id
        )


        await interaction.edit_original_response(
            content=(
                f"✅ **{self.target.display_name}** さんへ"
                "告白を送りました！\n\n"
                "相手が\n"
                "❤️ 承認する\n"
                "💔 お断りする\n"
                "のどちらかを選択すると、"
                "Botからあなたへ結果が届きます。"
            )
        )


        # 管理ログは裏で送信
        asyncio.create_task(
            send_log(
                interaction.client,
                guild.id,
                (
                    "💌 **告白送信**\n"
                    f"送信者：<@{sender.id}>\n"
                    f"受信者：<@{self.target.id}>\n"
                    f"告白ID：`{confession_id}`"
                )
            )
        )


# =========================================================
# 告白相手選択
# =========================================================

class TargetSelect(
    discord.ui.UserSelect
):

    def __init__(self):

        super().__init__(
            placeholder=(
                "告白する相手を選んでください"
            ),
            min_values=1,
            max_values=1
        )


    async def callback(
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

        selected = self.values[0]


        # -----------------------------------------
        # 自分自身
        # -----------------------------------------

        if selected.id == interaction.user.id:

            await interaction.response.send_message(
                "❌ 自分自身には告白できません。",
                ephemeral=True
            )

            return


        # -----------------------------------------
        # Bot
        # -----------------------------------------

        if selected.bot:

            await interaction.response.send_message(
                "❌ Botには告白できません。",
                ephemeral=True
            )

            return


        # -----------------------------------------
        # API通信をしない
        #
        # fetch_member() を使わないことで
        # 遅延を防止
        # -----------------------------------------

        if isinstance(
            selected,
            discord.Member
        ):

            target = selected

        else:

            target = guild.get_member(
                selected.id
            )


        if not target:

            await interaction.response.send_message(
                (
                    "❌ 相手の情報を取得できませんでした。\n"
                    "もう一度選択してください。"
                ),
                ephemeral=True
            )

            return


        if isinstance(
            interaction.user,
            discord.Member
        ):

            sender = interaction.user

        else:

            sender = guild.get_member(
                interaction.user.id
            )


        if not sender:

            await interaction.response.send_message(
                "❌ あなたの情報を取得できませんでした。",
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


        rule = settings[
            "target_rule"
        ]


        # -----------------------------------------
        # 性別制限
        # -----------------------------------------

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
                        "❌ あなたの性別ロールを"
                        "確認できませんでした。"
                    ),
                    ephemeral=True
                )

                return


            if not target_gender:

                await interaction.response.send_message(
                    (
                        "❌ 相手の性別ロールを"
                        "確認できませんでした。"
                    ),
                    ephemeral=True
                )

                return


            if (
                rule == "opposite"
                and sender_gender
                == target_gender
            ):

                await interaction.response.send_message(
                    (
                        "このサーバーでは"
                        "異性への告白のみ可能です。"
                    ),
                    ephemeral=True
                )

                return


            if (
                rule == "same"
                and sender_gender
                != target_gender
            ):

                await interaction.response.send_message(
                    (
                        "このサーバーでは"
                        "同性への告白のみ可能です。"
                    ),
                    ephemeral=True
                )

                return


        # -----------------------------------------
        # 即座にModal表示
        #
        # ここでは通信処理を挟まない
        # -----------------------------------------

        await interaction.response.send_modal(
            ConfessionModal(
                target=target
            )
        )


# =========================================================
# 相手選択画面
# =========================================================

class TargetSelectView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=120
        )

        self.add_item(
            TargetSelect()
        )


# =========================================================
# メイン告白パネル
# =========================================================

class ConfessionPanelView(
    discord.ui.View
):

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
                    "このサーバーでは"
                    "まだ設定されていません。\n"
                    "管理者にお問い合わせください。"
                ),
                ephemeral=True
            )

            return


        # -----------------------------------------
        # クールタイム確認
        # -----------------------------------------

        last_sent = get_cooldown(
            interaction.guild.id,
            interaction.user.id
        )

        cooldown = settings[
            "cooldown_seconds"
        ]


        if last_sent:

            elapsed = (
                int(time.time())
                - last_sent
            )

            if elapsed < cooldown:

                remain = (
                    cooldown
                    - elapsed
                )

                minutes = remain // 60
                seconds = remain % 60

                await interaction.response.send_message(
                    (
                        "⏱️ 次の告白まで\n"
                        f"**あと {minutes}分"
                        f"{seconds}秒** "
                        "お待ちください。"
                    ),
                    ephemeral=True
                )

                return


        # -----------------------------------------
        # 即座に相手選択画面を出す
        # 「考え中」を出さない
        # -----------------------------------------

        embed = discord.Embed(
            title="💌 告白する相手を選択",
            description=(
                "下のメニューから"
                "告白したい相手を"
                "1人選んでください。\n\n"
                "🔒 この画面は"
                "**あなたにだけ表示されています。**\n\n"
                "相手を選ぶと"
                "告白メッセージ入力画面が開きます。"
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
# Bot本体
# =========================================================

class KokuhakuBot(
    commands.Bot
):

    def __init__(self):

        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents
        )


    async def setup_hook(
        self
    ):

        init_database()


        # -----------------------------------------
        # メインボタン
        # 再起動後も有効
        # -----------------------------------------

        self.add_view(
            ConfessionPanelView()
        )


        # -----------------------------------------
        # 未回答告白を復元
        # -----------------------------------------

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


        # -----------------------------------------
        # スラッシュコマンド同期
        # -----------------------------------------

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


    async def on_ready(
        self
    ):

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
    パネルチャンネル=(
        "告白パネルを設置するチャンネル"
    ),
    男性ロール=(
        "男性用ロール"
    ),
    女性ロール=(
        "女性用ロール"
    ),
    告白対象=(
        "誰に告白できるか"
    ),
    クールタイム=(
        "告白後の待ち時間（分）"
    ),
    管理ログ=(
        "管理ログを送るチャンネル（任意）"
    )
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
    クールタイム: app_commands.Range[
        int,
        0,
        1440
    ] = 3,
    管理ログ: Optional[
        discord.TextChannel
    ] = None
):

    if not interaction.guild:
        return


    save_settings(
        guild_id=interaction.guild.id,
        panel_channel_id=(
            パネルチャンネル.id
        ),
        log_channel_id=(
            管理ログ.id
            if 管理ログ
            else None
        ),
        male_role_id=男性ロール.id,
        female_role_id=女性ロール.id,
        target_rule=告白対象.value,
        cooldown_seconds=(
            クールタイム * 60
        )
    )


    embed = discord.Embed(
        title="✅ 告白Bot設定完了",
        description=(
            "設定を保存しました。\n\n"
            "続けて `/告白パネル` を"
            "実行してください。"
        ),
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


    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# /告白パネル
# =========================================================

@bot.tree.command(
    name="告白パネル",
    description=(
        "設定したチャンネルに"
        "告白パネルを設置します"
    )
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
                "❌ 設定されている"
                "パネルチャンネルが"
                "見つかりません。\n\n"
                "`/告白設定` を"
                "やり直してください。"
            ),
            ephemeral=True
        )

        return


    cooldown_minutes = (
        settings[
            "cooldown_seconds"
        ]
        // 60
    )


    embed = discord.Embed(
        title="💌 匿名告白",
        description=(
            "気になる相手へ、"
            "Botを通して"
            "気持ちを届けられます。\n\n"
            "告白内容は"
            "サーバーには公開されません。\n"
            "選択した相手にのみ"
            "Botから通知されます。\n\n"
            "相手には"
            "**あなたの名前が表示されます。**\n\n"
            "相手が\n"
            "❤️ **承認する**\n"
            "💔 **お断りする**\n"
            "のどちらかを選択すると、"
            "結果がBotから"
            "あなたへ届きます。\n\n"
            f"⏱️ クールタイム："
            f"**{cooldown_minutes}分**\n"
            f"💞 告白対象："
            f"**{target_rule_text(settings['target_rule'])}**\n\n"
            "下のボタンから"
            "告白を開始してください。"
        ),
        color=discord.Color.from_rgb(
            255,
            105,
            180
        )
    )


    try:

        await asyncio.wait_for(
            channel.send(
                embed=embed,
                view=ConfessionPanelView()
            ),
            timeout=DM_TIMEOUT_SECONDS
        )


    except asyncio.TimeoutError:

        await interaction.response.send_message(
            (
                "❌ パネル設置に"
                "3秒以上かかったため"
                "処理を中止しました。"
            ),
            ephemeral=True
        )

        return


    except discord.Forbidden:

        await interaction.response.send_message(
            (
                "❌ パネルチャンネルに"
                "メッセージを送信する"
                "権限がありません。"
            ),
            ephemeral=True
        )

        return


    await interaction.response.send_message(
        (
            f"✅ {channel.mention} に"
            "告白パネルを設置しました。"
        ),
        ephemeral=True
    )


# =========================================================
# /告白設定確認
# =========================================================

@bot.tree.command(
    name="告白設定確認",
    description=(
        "現在の匿名告白Bot設定を確認します"
    )
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


    log_channel = None

    if settings["log_channel_id"]:

        log_channel = (
            interaction.guild.get_channel(
                settings[
                    "log_channel_id"
                ]
            )
        )


    male_role = (
        interaction.guild.get_role(
            settings["male_role_id"]
        )
    )


    female_role = (
        interaction.guild.get_role(
            settings["female_role_id"]
        )
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
# コマンドエラー
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    logger.error(
        "Application command error: %s",
        error
    )


    message = (
        "❌ コマンド実行中に"
        "エラーが発生しました。"
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
