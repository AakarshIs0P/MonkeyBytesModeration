"""
cogs/msg_stats.py — Message Statistics cog.

Features:
  !firstmessage [user]   — find a user's first message in this server
  /firstmessage [user]   — slash version
  !msgleaderboard        — top 10 message senders in this server
  /msgleaderboard        — slash version

Message counts are tracked live in data/msg_counts.json:
  { "guild_id": { "user_id": count } }

Uses asyncio.Lock to prevent concurrent write races.
Cog name is "MsgStats" to match COG_META key in utils/data.py.
"""

import json
import os
import asyncio
import logging
import discord

from datetime import timezone
from discord.ext import commands, tasks
from discord import app_commands
from utils.data import DiscordBot
from utils import default
from utils.default import CustomContext

log = logging.getLogger("bot.msg_stats")

MSG_FILE = "data/msg_counts.json"
COL = discord.Colour.blurple()

# Async lock — prevents concurrent write races in the event loop
_lock = asyncio.Lock()

# In-memory cache: { "guild_id": { "user_id": count } }
_cache: dict = {}
_dirty = False  # tracks whether cache has unsaved changes


# ── Storage helpers ────────────────────────────────────────────────────────────

def _ensure_data_dir():
    os.makedirs("data", exist_ok=True)


def _load_from_disk() -> dict:
    try:
        with open(MSG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_to_disk(data: dict):
    _ensure_data_dir()
    tmp = MSG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, MSG_FILE)


async def _async_increment(guild_id: int, user_id: int):
    """Async-safe increment using asyncio.Lock."""
    global _dirty
    async with _lock:
        gid = str(guild_id)
        uid = str(user_id)
        _cache.setdefault(gid, {})[uid] = _cache.get(gid, {}).get(uid, 0) + 1
        _dirty = True


async def _flush_cache():
    """Write the in-memory cache to disk if dirty."""
    global _dirty
    async with _lock:
        if not _dirty:
            return
        _save_to_disk(_cache)
        _dirty = False


# Public helpers (synchronous, safe to call from non-async contexts) ──────────

def get_msg_count(guild_id: int, user_id: int) -> int:
    """Used by discord_info userinfo embed."""
    gid = str(guild_id)
    uid = str(user_id)
    return _cache.get(gid, {}).get(uid, 0)


def get_guild_total(guild_id: int) -> int:
    """Used by discord_info serverinfo embed."""
    return sum(_cache.get(str(guild_id), {}).values())


def get_leaderboard(guild_id: int, top: int = 10) -> list:
    """Returns list of (user_id_str, count) sorted descending."""
    guild_data = _cache.get(str(guild_id), {})
    return sorted(guild_data.items(), key=lambda x: x[1], reverse=True)[:top]


# ── First-message search ───────────────────────────────────────────────────────

async def _find_first_message_in_channel(channel: discord.TextChannel) -> discord.Message | None:
    """Return the oldest message ever sent in a specific channel, or None."""
    perms = channel.permissions_for(channel.guild.me)
    if not (perms.read_messages and perms.read_message_history):
        return None
    try:
        async for msg in channel.history(limit=1, oldest_first=True):
            return msg
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


async def _find_first_message(guild: discord.Guild, user: discord.Member) -> discord.Message | None:
    """
    Quick scan: checks the oldest message per channel.
    Returns the earliest message by this user, or None.
    """
    earliest: discord.Message | None = None
    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not (perms.read_messages and perms.read_message_history):
            continue
        try:
            async for msg in channel.history(limit=1, oldest_first=True):
                if msg.author.id == user.id:
                    if earliest is None or msg.created_at < earliest.created_at:
                        earliest = msg
                    break
        except (discord.Forbidden, discord.HTTPException):
            continue
    return earliest


async def _find_first_message_deep(guild: discord.Guild, user: discord.Member) -> discord.Message | None:
    """
    Deep scan: reads oldest 100 messages per channel.
    Used as fallback when quick scan returns nothing.
    """
    earliest: discord.Message | None = None
    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not (perms.read_messages and perms.read_message_history):
            continue
        try:
            async for msg in channel.history(limit=100, oldest_first=True):
                if msg.author.id == user.id:
                    if earliest is None or msg.created_at < earliest.created_at:
                        earliest = msg
                    break
        except (discord.Forbidden, discord.HTTPException):
            continue
    return earliest


def _firstmsg_embed(user: discord.Member, msg: discord.Message | None) -> discord.Embed:
    if msg is None:
        return discord.Embed(
            description=f"❌ No messages found for **{user.display_name}** in this server.",
            colour=discord.Colour.red(),
        )
    ts = int(msg.created_at.replace(tzinfo=timezone.utc).timestamp())
    embed = discord.Embed(
        title=f"📜 First Message — {user.display_name}",
        colour=user.top_role.colour if user.top_role.colour.value else COL,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="📌 Channel", value=msg.channel.mention, inline=True)
    embed.add_field(name="🕐 Sent", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=True)
    embed.add_field(
        name="💬 Content",
        value=msg.content[:1000] if msg.content else "*[no text content]*",
        inline=False,
    )
    embed.add_field(name="🔗 Jump", value=f"[Click to jump]({msg.jump_url})", inline=False)
    embed.set_footer(text=f"User ID: {user.id}")
    return embed


def _channel_firstmsg_embed(channel: discord.TextChannel, msg: discord.Message | None) -> discord.Embed:
    if msg is None:
        return discord.Embed(
            description=f"❌ No messages found in {channel.mention} (missing permissions or empty channel).",
            colour=discord.Colour.red(),
        )
    ts = int(msg.created_at.replace(tzinfo=timezone.utc).timestamp())
    embed = discord.Embed(
        title=f"📜 First Message — #{channel.name}",
        colour=COL,
    )
    embed.add_field(name="👤 Author", value=msg.author.mention, inline=True)
    embed.add_field(name="🕐 Sent", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=True)
    embed.add_field(
        name="💬 Content",
        value=msg.content[:1000] if msg.content else "*[no text content]*",
        inline=False,
    )
    embed.add_field(name="🔗 Jump", value=f"[Click to jump]({msg.jump_url})", inline=False)
    embed.set_footer(text=f"Channel ID: {channel.id}")
    return embed


def _leaderboard_embed(guild: discord.Guild, entries: list, total: int) -> discord.Embed:
    embed = discord.Embed(title=f"🏆 Message Leaderboard — {guild.name}", colour=COL)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if not entries:
        embed.description = "No message data yet. Start chatting!"
        return embed
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, count) in enumerate(entries, 1):
        member = guild.get_member(int(uid))
        name = member.display_name if member else f"<@{uid}>"
        medal = medals[i - 1] if i <= 3 else f"`{i}.`"
        pct = (count / total * 100) if total else 0
        lines.append(f"{medal} **{name}** — {count:,} msgs ({pct:.1f}%)")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Total tracked messages: {total:,}")
    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class MsgStats(commands.Cog, name="MsgStats"):
    """Message statistics tracking cog."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot
        # Load existing data into cache on startup
        _cache.update(_load_from_disk())
        log.info(f"MsgStats loaded {sum(len(v) for v in _cache.values())} user records from disk.")
        self.flush_task.start()

    def cog_unload(self):
        self.flush_task.cancel()
        # Best-effort flush on unload
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_flush_cache())
        except Exception:
            pass

    @tasks.loop(minutes=2)
    async def flush_task(self):
        await _flush_cache()

    # ── Track every message ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await _async_increment(message.guild.id, message.author.id)

    # ── !firstmessage ─────────────────────────────────────────────────────────

    @commands.command(aliases=["firstmsg", "fm"])
    @commands.guild_only()
    async def firstmessage(self, ctx: CustomContext, *, target: str = None):
        """Find the first message of a user, channel, or yourself.

        Usage:
          !firstmessage            — your own first message
          !firstmessage @user      — a mentioned user's first message
          !firstmessage #channel   — the first message ever in a channel
        """
        channel = None
        user = None

        if target is None:
            user = ctx.author
        else:
            # Try to resolve as a text channel first
            try:
                channel = await commands.TextChannelConverter().convert(ctx, target)
            except commands.BadArgument:
                # Fall back to member resolution
                try:
                    user = await commands.MemberConverter().convert(ctx, target)
                except commands.BadArgument:
                    await ctx.send(f"❌ Could not find a user or channel matching `{target}`.")
                    return

        async with ctx.channel.typing():
            if channel is not None:
                msg = await _find_first_message_in_channel(channel)
                embed = _channel_firstmsg_embed(channel, msg)
            else:
                msg = await _find_first_message(ctx.guild, user)
                if msg is None:
                    msg = await _find_first_message_deep(ctx.guild, user)
                embed = _firstmsg_embed(user, msg)

        await ctx.send(embed=embed)

    # ── /firstmessage ─────────────────────────────────────────────────────────

    @app_commands.command(name="firstmessage", description="Find the first message of a user or channel.")
    @app_commands.describe(
        user="The user to look up (default: yourself)",
        channel="A channel to find its very first message",
    )
    @app_commands.guild_only()
    async def slash_firstmessage(
        self,
        interaction: discord.Interaction,
        user: discord.Member = None,
        channel: discord.TextChannel = None,
    ):
        await interaction.response.defer()
        if channel is not None:
            msg = await _find_first_message_in_channel(channel)
            embed = _channel_firstmsg_embed(channel, msg)
        else:
            user = user or interaction.user
            msg = await _find_first_message(interaction.guild, user)
            if msg is None:
                msg = await _find_first_message_deep(interaction.guild, user)
            embed = _firstmsg_embed(user, msg)
        await interaction.followup.send(embed=embed)

    # ── !msgleaderboard ───────────────────────────────────────────────────────

    @commands.command(aliases=["msglb", "topchat", "chatleaders"])
    @commands.guild_only()
    async def msgleaderboard(self, ctx: CustomContext):
        """Show the top 10 most active chatters in this server."""
        entries = get_leaderboard(ctx.guild.id)
        total = get_guild_total(ctx.guild.id)
        await ctx.send(embed=_leaderboard_embed(ctx.guild, entries, total))

    # ── /msgleaderboard ───────────────────────────────────────────────────────

    @app_commands.command(name="msgleaderboard", description="Top 10 most active chatters in this server.")
    @app_commands.guild_only()
    async def slash_msgleaderboard(self, interaction: discord.Interaction):
        entries = get_leaderboard(interaction.guild.id)
        total = get_guild_total(interaction.guild.id)
        await interaction.response.send_message(embed=_leaderboard_embed(interaction.guild, entries, total))


async def setup(bot):
    await bot.add_cog(MsgStats(bot))
