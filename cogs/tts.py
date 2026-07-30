"""Text-to-speech backed by Edge TTS's complete live voice catalog."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time

import discord
from discord import app_commands
from discord.ext import commands

import edge_tts


DEFAULT_VOICE = "en-US-JennyNeural"
VOICE_CACHE_SECONDS = 60 * 60 * 12
MAX_TEXT_LENGTH = 1_500
MAX_VOICES_PER_PAGE = 20
RATE_RE = re.compile(r"^[+-]\d+%$")
VOLUME_RE = re.compile(r"^[+-]\d+%$")
PITCH_RE = re.compile(r"^[+-]\d+Hz$")


def _error(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", colour=discord.Colour.red())


class TTS(commands.Cog):
    """Generate speech using every voice currently offered by Edge TTS."""

    def __init__(self, bot):
        self.bot = bot
        self._voices: dict[str, dict] = {}
        self._voices_loaded_at = 0.0
        self._voice_lock = asyncio.Lock()

    async def _get_voices(self, *, refresh: bool = False) -> dict[str, dict]:
        """Fetch Edge TTS voices once per cache window to avoid repeated API calls."""
        now = time.monotonic()
        if self._voices and not refresh and now - self._voices_loaded_at < VOICE_CACHE_SECONDS:
            return self._voices

        async with self._voice_lock:
            now = time.monotonic()
            if self._voices and not refresh and now - self._voices_loaded_at < VOICE_CACHE_SECONDS:
                return self._voices
            voices = await edge_tts.list_voices()
            self._voices = {
                voice["ShortName"]: voice
                for voice in voices
                if voice.get("ShortName")
            }
            self._voices_loaded_at = now
        return self._voices

    async def _resolve_voice(self, query: str) -> dict | None:
        voices = await self._get_voices()
        normalized = query.strip().casefold()
        for short_name, voice in voices.items():
            if short_name.casefold() == normalized:
                return voice
        return None

    @staticmethod
    def _voice_label(voice: dict) -> str:
        return (
            f"{voice['ShortName']} • {voice.get('Gender', 'Unknown')} • "
            f"{voice.get('Locale', 'Unknown locale')}"
        )

    @staticmethod
    def _validate_controls(rate: str, volume: str, pitch: str) -> str | None:
        if not RATE_RE.fullmatch(rate):
            return "Rate must look like `+10%` or `-20%`."
        if not VOLUME_RE.fullmatch(volume):
            return "Volume must look like `+0%` or `-10%`."
        if not PITCH_RE.fullmatch(pitch):
            return "Pitch must look like `+0Hz` or `-5Hz`."
        return None

    async def _generate_tts(self, text: str, voice: str, rate: str, volume: str, pitch: str) -> str:
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp_path = temp.name
        temp.close()
        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate,
                volume=volume,
                pitch=pitch,
            )
            await communicate.save(temp_path)
            return temp_path
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    async def _send_tts(
        self,
        send,
        *,
        requested_by: discord.abc.User,
        text: str,
        voice: dict,
        rate: str,
        volume: str,
        pitch: str,
    ) -> None:
        temp_path = None
        try:
            temp_path = await self._generate_tts(text, voice["ShortName"], rate, volume, pitch)
            embed = discord.Embed(title="🗣️ Text to Speech", colour=discord.Colour.dark_theme())
            embed.add_field(name="Text", value=text[:1_000], inline=False)
            embed.add_field(name="Voice", value=f"`{voice['ShortName']}`", inline=False)
            embed.add_field(name="Controls", value=f"Rate `{rate}` • Volume `{volume}` • Pitch `{pitch}`", inline=False)
            embed.set_footer(text=f"Requested by {requested_by}", icon_url=requested_by.display_avatar.url)
            await send(embed=embed, file=discord.File(temp_path, filename=f"tts-{requested_by.id}.mp3"))
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    @staticmethod
    def _extract_prefix_controls(text: str) -> tuple[str, str, str, str]:
        """Accept --rate, --volume, and --pitch at the end of a prefix command."""
        controls = {"rate": "+0%", "volume": "+0%", "pitch": "+0Hz"}
        for name in controls:
            match = re.search(rf"\s+--{name}\s+(\S+)", text, flags=re.IGNORECASE)
            if match:
                controls[name] = match.group(1)
                text = text[:match.start()] + text[match.end():]
        return text.strip(), controls["rate"], controls["volume"], controls["pitch"]

    @commands.command(name="tts")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def tts(self, ctx, voice_or_text: str, *, text: str | None = None):
        """Speak text. Use !ttsvoices to find any Edge TTS voice."""
        if text is None:
            voice_query, text = DEFAULT_VOICE, voice_or_text
        elif "-" not in voice_or_text:
            # Voice ShortNames always include a locale separator (for example
            # en-US-JennyNeural), so ordinary multi-word text remains easy to use.
            voice_query, text = DEFAULT_VOICE, f"{voice_or_text} {text}"
        else:
            voice_query = voice_or_text
        text, rate, volume, pitch = self._extract_prefix_controls(text)

        if not text:
            return await ctx.reply(embed=_error("Provide text to speak."))
        if len(text) > MAX_TEXT_LENGTH:
            return await ctx.reply(embed=_error(f"Text must be {MAX_TEXT_LENGTH:,} characters or fewer."))
        control_error = self._validate_controls(rate, volume, pitch)
        if control_error:
            return await ctx.reply(embed=_error(control_error))

        async with ctx.typing():
            try:
                voice = await self._resolve_voice(voice_query)
            except Exception:
                return await ctx.reply(embed=_error("Could not load Edge TTS voices. Please try again shortly."))
            if not voice:
                return await ctx.reply(
                    embed=_error(f"Unknown voice `{voice_query}`. Run `!ttsvoices <search>` to find a valid ShortName.")
                )
            try:
                await self._send_tts(
                    ctx.reply,
                    requested_by=ctx.author,
                    text=text,
                    voice=voice,
                    rate=rate,
                    volume=volume,
                    pitch=pitch,
                )
            except Exception:
                await ctx.reply(embed=_error("Edge TTS could not create audio for that request."))

    @app_commands.command(name="tts", description="Generate speech with any Edge TTS voice.")
    @app_commands.describe(
        text="Text to speak",
        voice="Edge TTS ShortName; autocomplete shows available voices",
        rate="For example +10% or -20%",
        volume="For example +0% or -10%",
        pitch="For example +0Hz or -5Hz",
    )
    async def slash_tts(
        self,
        interaction: discord.Interaction,
        text: str,
        voice: str = DEFAULT_VOICE,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
    ):
        if len(text) > MAX_TEXT_LENGTH:
            return await interaction.response.send_message(
                embed=_error(f"Text must be {MAX_TEXT_LENGTH:,} characters or fewer."), ephemeral=True
            )
        control_error = self._validate_controls(rate, volume, pitch)
        if control_error:
            return await interaction.response.send_message(embed=_error(control_error), ephemeral=True)

        await interaction.response.defer(thinking=True)
        try:
            resolved_voice = await self._resolve_voice(voice)
        except Exception:
            return await interaction.followup.send(embed=_error("Could not load Edge TTS voices. Please try again shortly."), ephemeral=True)
        if not resolved_voice:
            return await interaction.followup.send(
                embed=_error(f"Unknown voice `{voice}`. Use `/ttsvoices` to search the catalog."), ephemeral=True
            )
        try:
            await self._send_tts(
                interaction.followup.send,
                requested_by=interaction.user,
                text=text,
                voice=resolved_voice,
                rate=rate,
                volume=volume,
                pitch=pitch,
            )
        except Exception:
            await interaction.followup.send(embed=_error("Edge TTS could not create audio for that request."), ephemeral=True)

    @slash_tts.autocomplete("voice")
    async def voice_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            voices = await self._get_voices()
        except Exception:
            return []
        query = current.casefold()
        matches = [
            voice for voice in voices.values()
            if query in voice["ShortName"].casefold()
            or query in voice.get("Locale", "").casefold()
            or query in voice.get("Gender", "").casefold()
        ]
        matches.sort(key=lambda voice: voice["ShortName"])
        return [
            app_commands.Choice(name=self._voice_label(voice)[:100], value=voice["ShortName"])
            for voice in matches[:25]
        ]

    @commands.hybrid_command(name="ttsvoices", description="Search every voice currently available in Edge TTS.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @app_commands.describe(query="Filter by ShortName, locale, or gender", page="Page number")
    async def ttsvoices(self, ctx: commands.Context, query: str = "", page: int = 1):
        """List current Edge TTS voices; use locale terms such as en-US or hi-IN."""
        if page < 1:
            return await ctx.send(embed=_error("Page numbers start at 1."), ephemeral=True)
        try:
            voices = await self._get_voices()
        except Exception:
            return await ctx.send(embed=_error("Could not load the Edge TTS voice catalog."), ephemeral=True)

        needle = query.strip().casefold()
        matches = [
            voice for voice in voices.values()
            if not needle
            or needle in voice["ShortName"].casefold()
            or needle in voice.get("Locale", "").casefold()
            or needle in voice.get("Gender", "").casefold()
        ]
        matches.sort(key=lambda voice: voice["ShortName"])
        if not matches:
            return await ctx.send(embed=_error(f"No Edge TTS voices match `{query}`."), ephemeral=True)

        pages = (len(matches) + MAX_VOICES_PER_PAGE - 1) // MAX_VOICES_PER_PAGE
        if page > pages:
            return await ctx.send(embed=_error(f"There are only {pages} page(s) of matching voices."), ephemeral=True)
        start = (page - 1) * MAX_VOICES_PER_PAGE
        voice_lines = [f"`{voice['ShortName']}` — {voice.get('Gender', 'Unknown')}" for voice in matches[start:start + MAX_VOICES_PER_PAGE]]
        label = f" matching `{query}`" if query else ""
        embed = discord.Embed(
            title=f"🗣️ Edge TTS Voices{label}",
            description="\n".join(voice_lines),
            colour=discord.Colour.dark_theme(),
        )
        embed.set_footer(text=f"Page {page}/{pages} • {len(matches)} voices • Use the ShortName with !tts")
        await ctx.send(embed=embed, ephemeral=True)

    @tts.error
    async def tts_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(embed=_error(f"Slow down. Try again in `{error.retry_after:.1f}s`."))
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(embed=_error("Usage: `!tts [voice] <text>` — run `!ttsvoices` to find voices."))
            return
        raise error


async def setup(bot):
    await bot.add_cog(TTS(bot))
