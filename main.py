from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# 寝落ち移動Bot
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# 寝落ち部屋VC ID
SLEEP_VC_ID = 1540961169517314129


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("sleep-move-bot")


# =========================================================
# Bot設定
# =========================================================

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# 確認ボタン
# =========================================================

class SleepMoveConfirmView(discord.ui.View):

    def __init__(
        self,
        target_user: discord.Member,
        requester: discord.Member
    ):
        super().__init__(timeout=60)

        self.target_user = target_user
        self.requester = requester
        self.finished = False

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        # 対象本人だけ押せる
        if interaction.user.id != self.target_user.id:

            await interaction.response.send_message(
                "このボタンは移動対象の本人だけ押せます。",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="寝落ち部屋へ移動する",
        emoji="🌙",
        style=discord.ButtonStyle.success
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.finished:
            return

        self.finished = True

        guild = interaction.guild

        if guild is None:
            return

        # 寝落ちVC取得
        sleep_vc = guild.get_channel(
            SLEEP_VC_ID
        )

        if not isinstance(
            sleep_vc,
            discord.VoiceChannel
        ):

            await interaction.response.send_message(
                "❌ 寝落ち部屋が見つかりません。",
                ephemeral=True
            )

            return

        # 対象がVCにいるか確認
        if (
            not self.target_user.voice
            or not self.target_user.voice.channel
        ):

            await interaction.response.send_message(
                "❌ 今VCに入っていないので移動できません。",
                ephemeral=True
            )

            return

        # すでに寝落ちVCにいる
        if self.target_user.voice.channel.id == SLEEP_VC_ID:

            await interaction.response.send_message(
                "🌙 すでに寝落ち部屋にいます。",
                ephemeral=True
            )

            return

        try:

            # 対象を移動
            await self.target_user.move_to(
                sleep_vc,
                reason=(
                    f"寝落ち移動コマンド "
                    f"requester={self.requester.id}"
                )
            )

            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(
                content=(
                    f"🌙 {self.target_user.mention} を "
                    f"{sleep_vc.mention} に移動しました！"
                ),
                view=self
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                "❌ Botに「メンバーを移動」権限がありません。",
                ephemeral=True
            )

        except discord.HTTPException:

            await interaction.response.send_message(
                "❌ 移動に失敗しました。",
                ephemeral=True
            )

    @discord.ui.button(
        label="移動しない",
        emoji="✖️",
        style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.finished:
            return

        self.finished = True

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"✖️ {self.target_user.mention} は "
                "寝落ち部屋への移動をキャンセルしました。"
            ),
            view=self
        )

    async def on_timeout(self):

        if self.finished:
            return

        self.finished = True

        for child in self.children:
            child.disabled = True


# =========================================================
# 寝落ち移動コマンド
# =========================================================

@bot.tree.command(
    name="寝落ち移動",
    description="指定した人を寝落ち部屋へ移動します"
)
@app_commands.describe(
    ユーザー="寝落ち部屋へ移動したい人"
)
async def sleep_move(
    interaction: discord.Interaction,
    ユーザー: discord.Member
):

    if interaction.guild is None:
        return

    # Botは対象外
    if ユーザー.bot:

        await interaction.response.send_message(
            "Botは移動対象にできません。",
            ephemeral=True
        )

        return

    # 対象がVCにいない
    if (
        not ユーザー.voice
        or not ユーザー.voice.channel
    ):

        await interaction.response.send_message(
            f"❌ {ユーザー.mention} は今VCにいません。",
            ephemeral=True
        )

        return

    # すでに寝落ちVC
    if ユーザー.voice.channel.id == SLEEP_VC_ID:

        await interaction.response.send_message(
            f"🌙 {ユーザー.mention} はすでに寝落ち部屋にいます。",
            ephemeral=True
        )

        return

    requester = interaction.user

    if not isinstance(
        requester,
        discord.Member
    ):
        return

    view = SleepMoveConfirmView(
        target_user=ユーザー,
        requester=requester
    )

    await interaction.response.send_message(
        (
            f"🌙 {ユーザー.mention}\n\n"
            f"{requester.mention} さんから"
            "寝落ち部屋への移動リクエストが届きました。\n\n"
            "移動してもよければ下のボタンを押してください。"
        ),
        view=view
    )


# =========================================================
# 起動
# =========================================================

@bot.event
async def on_ready():

    try:

        synced = await bot.tree.sync()

        log.info(
            "コマンド同期完了: %s個",
            len(synced)
        )

    except Exception:
        log.exception(
            "コマンド同期失敗"
        )

    log.info(
        "ログイン成功: %s",
        bot.user
    )


# =========================================================
# Start
# =========================================================

if __name__ == "__main__":

    if not TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN が設定されていません。"
        )

    bot.run(
        TOKEN
    )
