# cogs/tts.py

import os
import asyncio
import tempfile

import discord
from discord.ext import commands
from discord import app_commands

import edge_tts


LANGS = {
    "english": ("en-US-GuyNeural", "English"),
    "female": ("en-US-JennyNeural", "Female English"),
    "hindi": ("hi-IN-MadhurNeural", "Hindi"),
    "japanese": ("ja-JP-KeitaNeural", "Japanese"),
}


class TTS(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def generate_tts(self, text: str, voice: str):
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp_path = temp.name
        temp.close()

        communicate = edge_tts.Communicate(
            text=text,
            voice=voice
        )

        await communicate.save(temp_path)

        return temp_path

    # =========================
    # PREFIX COMMAND
    # =========================

    @commands.command(name="tts")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def tts(self, ctx, language="english", *, text=None):

        if text is None:
            text = language
            language = "english"

        if len(text) > 500:
            return await ctx.reply("❌ Max 500 characters.")

        language = language.lower()

        if language not in LANGS:
            langs = ", ".join(LANGS.keys())
            return await ctx.reply(
                f"❌ Invalid language.\nAvailable:\n```{langs}```"
            )

        voice, display = LANGS[language]

        async with ctx.typing():

            temp_path = None

            try:
                temp_path = await self.generate_tts(text, voice)

                file = discord.File(
                    temp_path,
                    filename=f"tts-{ctx.author.id}.mp3"
                )

                embed = discord.Embed(
                    title="🗣️ Text To Speech",
                    color=0x2F3136
                )

                embed.add_field(
                    name="Text",
                    value=f"```{text[:1000]}```",
                    inline=False
                )

                embed.add_field(
                    name="Voice",
                    value=display,
                    inline=True
                )

                embed.set_footer(
                    text=f"Requested by {ctx.author}"
                )

                await ctx.reply(
                    embed=embed,
                    file=file
                )

            except Exception as e:
                await ctx.reply(
                    f"❌ Error:\n```py\n{e}\n```"
                )

            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

    # =========================
    # SLASH COMMAND
    # =========================

    @app_commands.command(
        name="tts",
        description="Generate a TTS voice message"
    )
    async def slash_tts(
        self,
        interaction: discord.Interaction,
        text: str,
        language: str = "english"
    ):

        if len(text) > 500:
            return await interaction.response.send_message(
                "❌ Max 500 characters.",
                ephemeral=True
            )

        language = language.lower()

        if language not in LANGS:
            langs = ", ".join(LANGS.keys())

            return await interaction.response.send_message(
                f"❌ Invalid language.\n```{langs}```",
                ephemeral=True
            )

        voice, display = LANGS[language]

        await interaction.response.defer()

        temp_path = None

        try:
            temp_path = await self.generate_tts(text, voice)

            file = discord.File(
                temp_path,
                filename=f"tts-{interaction.user.id}.mp3"
            )

            embed = discord.Embed(
                title="🗣️ Text To Speech",
                color=0x2F3136
            )

            embed.add_field(
                name="Text",
                value=f"```{text[:1000]}```",
                inline=False
            )

            embed.add_field(
                name="Voice",
                value=display,
                inline=True
            )

            embed.set_footer(
                text=f"Requested by {interaction.user}"
            )

            await interaction.followup.send(
                embed=embed,
                file=file
            )

        except Exception as e:
            await interaction.followup.send(
                f"❌ Error:\n```py\n{e}\n```"
            )

        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    # =========================
    # ERROR HANDLER
    # =========================

    @tts.error
    async def tts_error(self, ctx, error):

        if isinstance(error, commands.CommandOnCooldown):

            await ctx.reply(
                f"⏳ Slow down.\nTry again in `{round(error.retry_after, 1)}s`."
            )


async def setup(bot):
    await bot.add_cog(TTS(bot))