import discord
from discord.ext import commands
from discord import app_commands
from utils.default import CustomContext
from utils.data import DiscordBot

from deep_translator import GoogleTranslator

ACCENT = discord.Colour.from_str("#5865F2")


# ── Supported Languages ──────────────────────────────────────────────
LANGUAGES = {
    "auto": "Auto Detect",
    "en": "English",
    "hi": "Hindi",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "ru": "Russian",
    "ar": "Arabic",
    "pt": "Portuguese",
    "tr": "Turkish",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "fi": "Finnish",
    "no": "Norwegian",
    "da": "Danish",
    "cs": "Czech",
    "el": "Greek",
    "he": "Hebrew",
    "id": "Indonesian",
    "th": "Thai",
    "vi": "Vietnamese",
    "uk": "Ukrainian",
    "ro": "Romanian",
    "hu": "Hungarian",
    "bg": "Bulgarian"
}


class Translation(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot

    # ── Logic ────────────────────────────────────────────────────────

    async def _translate_embed(self, text: str, target: str, source: str | None = None) -> discord.Embed:
        try:
            if source and source != "auto":
                translated = GoogleTranslator(source=source, target=target).translate(text)
                detected = source
            else:
                translated = GoogleTranslator(target=target).translate(text)
                detected = "auto"
        except Exception:
            return discord.Embed(
                description="❌ Translation failed. Try again later.",
                colour=discord.Colour.red()
            )

        embed = discord.Embed(title="🌐 Translation", colour=ACCENT)
        embed.add_field(name="📝 Original", value=text, inline=False)
        embed.add_field(name="🔁 Translated", value=translated, inline=False)

        src_name = LANGUAGES.get(detected, detected)
        tgt_name = LANGUAGES.get(target, target)

        embed.set_footer(text=f"{src_name} → {tgt_name}")
        return embed

    # ── Prefix Command ───────────────────────────────────────────────

    @commands.command(name="translate", aliases=["tr"])
    @commands.cooldown(1, 2.0, commands.BucketType.user)
    async def translate(self, ctx: CustomContext, target: str, *, text: str):
        """
        Example: !translate hi Hello
        """
        if target not in LANGUAGES:
            return await ctx.send("❌ Invalid language code. Use something like `en`, `hi`, `fr`.")

        async with ctx.channel.typing():
            embed = await self._translate_embed(text, target)
            await ctx.send(embed=embed)

    # ── Autocomplete ─────────────────────────────────────────────────

    async def language_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{name} ({code})", value=code)
            for code, name in LANGUAGES.items()
            if current.lower() in name.lower() or current.lower() in code.lower()
        ][:25]

    # ── Slash Command ────────────────────────────────────────────────

    @app_commands.command(name="translate", description="Translate text")
    @app_commands.describe(
        text="Text to translate",
        target="Target language",
        source="Source language (optional)"
    )
    @app_commands.autocomplete(target=language_autocomplete, source=language_autocomplete)
    async def slash_translate(
        self,
        interaction: discord.Interaction,
        text: str,
        target: str,
        source: str = "auto"
    ):
        await interaction.response.defer()

        if target not in LANGUAGES:
            return await interaction.followup.send("❌ Invalid language.")

        embed = await self._translate_embed(text, target, source)
        await interaction.followup.send(embed=embed)

    def help_embed(self, prefix: str = "!", guild=None) -> discord.Embed:
        embed = discord.Embed(
            title="Translator",
            description="Translate text to another language from prefix or slash commands.",
            colour=ACCENT,
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{prefix}translate <language> <text>`\n"
                f"`{prefix}tr <language> <text>`\n"
                "`/translate text:<text> target:<language> source:[language]`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Examples",
            value=(
                f"`{prefix}translate hi Hello, how are you?`\n"
                f"`{prefix}tr fr Good morning`\n"
                "`/translate text:Hello target:es`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Common Language Codes",
            value="`en` English, `hi` Hindi, `fr` French, `es` Spanish, `de` German, `ja` Japanese",
            inline=False,
        )
        return embed


async def setup(bot):
    await bot.add_cog(Translation(bot))
