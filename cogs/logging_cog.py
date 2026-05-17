import discord
import json
import os

from datetime import datetime, timezone
from discord.ext import commands
from discord import app_commands
from utils.default import CustomContext
from utils.data import DiscordBot
from utils import permissions

LOG_FILE = "data/log_channels.json"

# ── Colours per event category ─────────────────────────────────────────────────
C_JOIN    = discord.Colour.from_str("#2ECC71")   # green
C_LEAVE   = discord.Colour.from_str("#E74C3C")   # red
C_BAN     = discord.Colour.from_str("#C0392B")   # dark red
C_UNBAN   = discord.Colour.from_str("#27AE60")   # teal green
C_KICK    = discord.Colour.from_str("#E67E22")   # orange
C_MSG_DEL = discord.Colour.from_str("#E74C3C")   # red
C_MSG_EDT = discord.Colour.from_str("#F39C12")   # amber
C_ROLE    = discord.Colour.from_str("#9B59B6")   # purple
C_NICK    = discord.Colour.from_str("#3498DB")   # blue
C_CHAN    = discord.Colour.from_str("#1ABC9C")   # teal
C_VOICE   = discord.Colour.from_str("#95A5A6")   # grey
C_MUTE    = discord.Colour.from_str("#E67E22")   # orange
C_UNMUTE  = discord.Colour.from_str("#2ECC71")   # green
C_WARN    = discord.Colour.from_str("#F1C40F")   # yellow
C_PALADIN = discord.Colour.from_str("#F1C40F")   # yellow


# ── Persistence helpers ────────────────────────────────────────────────────────

def load_log_channels() -> dict:
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            json.dump({}, f)
    with open(LOG_FILE) as f:
        return json.load(f)


def save_log_channels(data: dict):
    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_log_channel(bot, guild_id: int):
    data       = load_log_channels()
    channel_id = data.get(str(guild_id))
    return bot.get_channel(channel_id) if channel_id else None


# ── Embed factory ──────────────────────────────────────────────────────────────

def log_embed(title: str, colour: discord.Colour, description: str = None) -> discord.Embed:
    e = discord.Embed(title=title, colour=colour, timestamp=datetime.now(timezone.utc))
    if description:
        e.description = description
    return e


def _ts(dt) -> str:
    """Discord relative + absolute timestamp."""
    if dt is None:
        return "Unknown"
    ts = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(dt)
    return f"<t:{ts}:F> (<t:{ts}:R>)"


def _short_ts(dt) -> str:
    if dt is None:
        return "Unknown"
    ts = int(dt.timestamp())
    return f"<t:{ts}:R>"


# ── Cog ────────────────────────────────────────────────────────────────────────

class Logging(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot
        self._cache: dict = load_log_channels()  # in-memory cache to avoid disk I/O per event

    # ── Setup commands ────────────────────────────────────────────────

    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(manage_guild=True)
    async def setlog(self, ctx: CustomContext, channel: discord.TextChannel = None):
        """ Set the log channel for this server. """
        channel = channel or ctx.channel
        data    = load_log_channels()
        data[str(ctx.guild.id)] = channel.id
        save_log_channels(data)
        self._cache = data  # update cache
        embed = discord.Embed(title="✅  Log Channel Set", colour=discord.Colour.green(),
            description=f"All events will now be logged in {channel.mention}.")
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(manage_guild=True)
    async def unsetlog(self, ctx: CustomContext):
        """ Disable logging for this server. """
        data = load_log_channels()
        data.pop(str(ctx.guild.id), None)
        save_log_channels(data)
        self._cache = data  # update cache
        embed = discord.Embed(title="🗑️  Logging Disabled", colour=discord.Colour.orange(),
            description="Log channel removed. No events will be logged.")
        await ctx.send(embed=embed)

    @app_commands.command(name="setlog", description="Set the log channel for this server.")
    @app_commands.describe(channel="Channel to send logs in (default: current channel)")
    @permissions.slash_has_permissions(manage_guild=True)
    async def slash_setlog(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        channel = channel or interaction.channel
        data    = load_log_channels()
        data[str(interaction.guild_id)] = channel.id
        save_log_channels(data)
        self._cache = data  # update cache
        embed = discord.Embed(title="✅  Log Channel Set", colour=discord.Colour.green(),
            description=f"Events will now be logged in {channel.mention}.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="unsetlog", description="Disable logging for this server.")
    @permissions.slash_has_permissions(manage_guild=True)
    async def slash_unsetlog(self, interaction: discord.Interaction):
        data = load_log_channels()
        data.pop(str(interaction.guild_id), None)
        save_log_channels(data)
        self._cache = data  # update cache
        embed = discord.Embed(title="🗑️  Logging Disabled", colour=discord.Colour.orange(),
            description="Log channel removed.")
        await interaction.response.send_message(embed=embed)

    # ── Internal send helper ──────────────────────────────────────────

    async def _log(self, guild_id: int, embed: discord.Embed, file: discord.File = None):
        channel_id = self._cache.get(str(guild_id))
        ch = self.bot.get_channel(channel_id) if channel_id else None
        if not ch:
            return
        try:
            await ch.send(embed=embed, file=file)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── Messages ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        embed = log_embed("🗑️  Message Deleted", C_MSG_DEL)
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="👤 Author",  value=f"{message.author.mention} `{message.author}` (`{message.author.id}`)", inline=False)
        embed.add_field(name="📺 Channel", value=f"{message.channel.mention} (`#{message.channel.name}`)", inline=True)
        embed.add_field(name="🕐 Sent",    value=_short_ts(message.created_at), inline=True)

        if message.content:
            content = message.content[:1020] + "…" if len(message.content) > 1024 else message.content
            embed.add_field(name="📝 Content", value=f"```{content}```", inline=False)
        else:
            embed.add_field(name="📝 Content", value="*No text content*", inline=False)

        if message.attachments:
            embed.add_field(name="📎 Attachments", value="\n".join(a.filename for a in message.attachments), inline=False)
        if message.embeds:
            embed.add_field(name="🖼️ Had Embeds", value=str(len(message.embeds)), inline=True)

        embed.set_footer(text=f"Message ID: {message.id}  •  User ID: {message.author.id}")
        await self._log(message.guild.id, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot or before.content == after.content:
            return
        embed = log_embed("✏️  Message Edited", C_MSG_EDT)
        embed.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        embed.add_field(name="👤 Author",  value=f"{before.author.mention} `{before.author}` (`{before.author.id}`)", inline=False)
        embed.add_field(name="📺 Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="🔗 Jump",    value=f"[View Message]({after.jump_url})", inline=True)
        embed.add_field(name="Before", value=f"```{before.content[:500] or '*empty*'}```", inline=False)
        embed.add_field(name="After",  value=f"```{after.content[:500]  or '*empty*'}```", inline=False)
        embed.set_footer(text=f"Message ID: {before.id}  •  User ID: {before.author.id}")
        await self._log(before.guild.id, embed)

    # ── Members ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        account_age_days = (datetime.now(timezone.utc) - member.created_at).days
        is_new           = account_age_days < 7

        embed = log_embed("📥  Member Joined", C_JOIN)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="👤 Member",       value=f"{member.mention}\n`{member}` (`{member.id}`)", inline=False)
        embed.add_field(name="📅 Account Created", value=_ts(member.created_at), inline=False)
        embed.add_field(name="🗓️ Account Age",  value=f"`{account_age_days}` days old", inline=True)
        embed.add_field(name="👥 Member #",     value=f"`{member.guild.member_count}`", inline=True)

        if is_new:
            embed.add_field(name="⚠️ Warning", value="**New account** — less than 7 days old!", inline=False)
            embed.colour = C_WARN

        embed.set_footer(text=f"User ID: {member.id}")
        await self._log(member.guild.id, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # Skip if Paladin AutoMod issued this kick (it logs it directly)
        paladin = self.bot.cogs.get("Paladin")
        if paladin and member.id in paladin.automod_acted.get(member.guild.id, set()):
            paladin.automod_acted[member.guild.id].discard(member.id)
            return

        # Check if this was actually a kick via audit log
        try:
            async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                if entry.target.id == member.id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    embed = log_embed("👢  Member Kicked", C_KICK)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                    embed.add_field(name="👤 Member",    value=f"{member.mention}\n`{member}` (`{member.id}`)", inline=False)
                    embed.add_field(name="🛡️ Moderator", value=f"{entry.user.mention} `{entry.user}` (`{entry.user.id}`)", inline=False)
                    embed.add_field(name="📝 Reason",    value=entry.reason or "No reason provided", inline=False)
                    roles = [r.mention for r in member.roles if r != member.guild.default_role]
                    if roles:
                        embed.add_field(name="🎭 Had Roles", value=", ".join(roles)[:1024], inline=False)
                    embed.add_field(name="📥 Joined",    value=_ts(member.joined_at), inline=True)
                    embed.set_footer(text=f"User ID: {member.id}")
                    await self._log(member.guild.id, embed)
                    return
        except discord.Forbidden:
            pass

        # Normal leave
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        embed = log_embed("📤  Member Left", C_LEAVE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="👤 Member",    value=f"{member.mention}\n`{member}` (`{member.id}`)", inline=False)
        embed.add_field(name="📥 Joined",    value=_ts(member.joined_at), inline=True)
        embed.add_field(name="👥 Remaining", value=f"`{member.guild.member_count}`", inline=True)
        if roles:
            embed.add_field(name="🎭 Roles", value=", ".join(roles)[:1024], inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        await self._log(member.guild.id, embed)

    # ── Bans ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        # Skip if Paladin AutoMod issued this ban (it logs it directly)
        paladin = self.bot.cogs.get("Paladin")
        if paladin and user.id in paladin.automod_acted.get(guild.id, set()):
            paladin.automod_acted[guild.id].discard(user.id)
            return

        moderator = None
        reason    = "No reason provided"
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                if entry.target.id == user.id:
                    moderator = entry.user
                    reason    = entry.reason or "No reason provided"
                    break
        except discord.Forbidden:
            pass

        embed = log_embed("🔨  Member Banned", C_BAN)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.add_field(name="👤 User",      value=f"{user.mention}\n`{user}` (`{user.id}`)", inline=False)
        if moderator:
            embed.add_field(name="🛡️ Moderator", value=f"{moderator.mention} `{moderator}` (`{moderator.id}`)", inline=False)
        embed.add_field(name="📝 Reason",    value=reason, inline=False)
        embed.set_footer(text=f"User ID: {user.id}")
        await self._log(guild.id, embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        moderator = None
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.unban):
                if entry.target.id == user.id:
                    moderator = entry.user
                    break
        except discord.Forbidden:
            pass

        embed = log_embed("✅  Member Unbanned", C_UNBAN)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.add_field(name="👤 User",      value=f"`{user}` (`{user.id}`)", inline=True)
        if moderator:
            embed.add_field(name="🛡️ Moderator", value=f"{moderator.mention} `{moderator}`", inline=True)
        embed.set_footer(text=f"User ID: {user.id}")
        await self._log(guild.id, embed)

    # ── Member Updates ────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild_id = before.guild.id

        # ── Nickname change ──
        if before.nick != after.nick:
            embed = log_embed("✏️  Nickname Changed", C_NICK)
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name="👤 Member", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Before",    value=f"`{before.nick or 'None'}`",      inline=True)
            embed.add_field(name="After",     value=f"`{after.nick  or 'None'}`",      inline=True)

            # Try to get who changed it from audit log
            try:
                async for entry in before.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_update):
                    if entry.target.id == before.id:
                        embed.add_field(name="🛡️ Changed by", value=entry.user.mention, inline=True)
                        break
            except discord.Forbidden:
                pass

            embed.set_footer(text=f"User ID: {after.id}")
            await self._log(guild_id, embed)

        # ── Role changes ──
        added   = [r for r in after.roles  if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]

        # Mute/unmute via Muted role
        for role in added:
            if role.name == "Muted":
                embed = log_embed("🔇  Member Muted", C_MUTE)
                embed.set_author(name=str(after), icon_url=after.display_avatar.url)
                embed.add_field(name="👤 Member", value=f"{after.mention} `{after}` (`{after.id}`)", inline=False)
                try:
                    async for entry in before.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_role_update):
                        if entry.target.id == after.id:
                            embed.add_field(name="🛡️ Muted by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                            if entry.reason:
                                embed.add_field(name="📝 Reason", value=entry.reason, inline=True)
                            break
                except discord.Forbidden:
                    pass
                embed.set_footer(text=f"User ID: {after.id}")
                await self._log(guild_id, embed)

        for role in removed:
            if role.name == "Muted":
                embed = log_embed("🔊  Member Unmuted", C_UNMUTE)
                embed.set_author(name=str(after), icon_url=after.display_avatar.url)
                embed.add_field(name="👤 Member", value=f"{after.mention} `{after}` (`{after.id}`)", inline=False)
                try:
                    async for entry in before.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_role_update):
                        if entry.target.id == after.id:
                            embed.add_field(name="🛡️ Unmuted by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                            break
                except discord.Forbidden:
                    pass
                embed.set_footer(text=f"User ID: {after.id}")
                await self._log(guild_id, embed)

        # General role add/remove
        other_added   = [r for r in added   if r.name != "Muted"]
        other_removed = [r for r in removed if r.name != "Muted"]
        if other_added or other_removed:
            embed = log_embed("🎭  Roles Updated", C_ROLE)
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name="👤 Member", value=f"{after.mention} `{after}` (`{after.id}`)", inline=False)
            if other_added:
                embed.add_field(name="➕ Added",   value=", ".join(r.mention for r in other_added)[:1024],   inline=False)
            if other_removed:
                embed.add_field(name="➖ Removed", value=", ".join(r.mention for r in other_removed)[:1024], inline=False)
            # Who did it
            try:
                async for entry in before.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_role_update):
                    if entry.target.id == after.id:
                        embed.add_field(name="🛡️ Updated by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                        break
            except discord.Forbidden:
                pass
            embed.set_footer(text=f"User ID: {after.id}")
            await self._log(guild_id, embed)

    # ── Paladin punishment log (called from paladin.py) ───────────────

    async def log_paladin_strip(self, guild: discord.Guild, actor: discord.Member, roles_removed: list, reason: str):
        embed = log_embed("🛡️  Paladin — Roles Stripped", C_PALADIN)
        embed.set_thumbnail(url=actor.display_avatar.url)
        embed.set_author(name=str(actor), icon_url=actor.display_avatar.url)
        embed.add_field(name="👤 Member",        value=f"{actor.mention} `{actor}` (`{actor.id}`)",          inline=False)
        embed.add_field(name="📝 Trigger",        value=reason,                                                inline=False)
        embed.add_field(name="🔑 Roles Stripped", value=", ".join(r.mention for r in roles_removed)[:1024],   inline=False)
        embed.add_field(name="🔢 Total Removed",  value=str(len(roles_removed)),                              inline=True)
        embed.set_footer(text=f"User ID: {actor.id}  •  Paladin Protection")
        await self._log(guild.id, embed)

    # ── Channel Events ────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        embed = log_embed("📺  Channel Created", C_CHAN)
        embed.add_field(name="📺 Channel",  value=f"{channel.mention} `#{channel.name}`", inline=True)
        embed.add_field(name="📂 Category", value=channel.category.name if channel.category else "None", inline=True)
        embed.add_field(name="🔧 Type",     value=str(channel.type).replace("_", " ").title(), inline=True)
        try:
            async for entry in channel.guild.audit_logs(limit=3, action=discord.AuditLogAction.channel_create):
                if entry.target.id == channel.id:
                    embed.add_field(name="🛡️ Created by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                    break
        except discord.Forbidden:
            pass
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._log(channel.guild.id, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        embed = log_embed("🗑️  Channel Deleted", C_MSG_DEL)
        embed.add_field(name="📺 Channel",  value=f"`#{channel.name}`", inline=True)
        embed.add_field(name="📂 Category", value=channel.category.name if channel.category else "None", inline=True)
        embed.add_field(name="🔧 Type",     value=str(channel.type).replace("_", " ").title(), inline=True)
        try:
            async for entry in channel.guild.audit_logs(limit=3, action=discord.AuditLogAction.channel_delete):
                if entry.target.id == channel.id:
                    embed.add_field(name="🛡️ Deleted by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                    break
        except discord.Forbidden:
            pass
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._log(channel.guild.id, embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if hasattr(before, "topic") and before.topic != after.topic:
            changes.append(f"**Topic:** `{before.topic or 'None'}` → `{after.topic or 'None'}`")
        if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
            changes.append(f"**Slowmode:** `{before.slowmode_delay}s` → `{after.slowmode_delay}s`")
        if hasattr(before, "nsfw") and before.nsfw != after.nsfw:
            changes.append(f"**NSFW:** `{before.nsfw}` → `{after.nsfw}`")
        if not changes:
            return
        embed = log_embed("📺  Channel Updated", C_CHAN)
        embed.add_field(name="📺 Channel", value=after.mention, inline=True)
        embed.add_field(name="📝 Changes", value="\n".join(changes), inline=False)
        try:
            async for entry in before.guild.audit_logs(limit=3, action=discord.AuditLogAction.channel_update):
                if entry.target.id == after.id:
                    embed.add_field(name="🛡️ Updated by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                    break
        except discord.Forbidden:
            pass
        embed.set_footer(text=f"Channel ID: {after.id}")
        await self._log(before.guild.id, embed)

    # ── Voice ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            # Check for mute/deafen changes
            changes = []
            if before.self_mute != after.self_mute:
                changes.append(f"Self-mute: `{before.self_mute}` → `{after.self_mute}`")
            if before.self_deaf != after.self_deaf:
                changes.append(f"Self-deaf: `{before.self_deaf}` → `{after.self_deaf}`")
            if before.mute != after.mute:
                changes.append(f"Server mute: `{before.mute}` → `{after.mute}`")
            if before.deaf != after.deaf:
                changes.append(f"Server deaf: `{before.deaf}` → `{after.deaf}`")
            if not changes:
                return
            embed = log_embed("🎙️  Voice State Changed", C_VOICE)
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.add_field(name="👤 Member",  value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="🔊 Channel", value=before.channel.name if before.channel else "—", inline=True)
            embed.add_field(name="📝 Changes", value="\n".join(changes), inline=False)
            embed.set_footer(text=f"User ID: {member.id}")
            await self._log(member.guild.id, embed)
            return

        if before.channel is None:
            embed = log_embed("🎙️  Joined Voice", C_JOIN)
            embed.add_field(name="👤 Member",  value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="🔊 Channel", value=f"`{after.channel.name}`",            inline=True)
        elif after.channel is None:
            embed = log_embed("🎙️  Left Voice", C_LEAVE)
            embed.add_field(name="👤 Member",  value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="🔊 Channel", value=f"`{before.channel.name}`",           inline=True)
        else:
            embed = log_embed("🎙️  Switched Voice Channel", C_VOICE)
            embed.add_field(name="👤 Member", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="From",      value=f"`{before.channel.name}`",           inline=True)
            embed.add_field(name="To",        value=f"`{after.channel.name}`",            inline=True)

        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_footer(text=f"User ID: {member.id}")
        await self._log(member.guild.id, embed)

    # ── Guild / Server Updates ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if before.icon != after.icon:
            changes.append("**Icon:** changed")
        if before.verification_level != after.verification_level:
            changes.append(f"**Verification:** `{before.verification_level}` → `{after.verification_level}`")
        if not changes:
            return
        embed = log_embed("🏠  Server Updated", C_CHAN)
        embed.add_field(name="📝 Changes", value="\n".join(changes), inline=False)
        try:
            async for entry in after.audit_logs(limit=3, action=discord.AuditLogAction.guild_update):
                embed.add_field(name="🛡️ Updated by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                break
        except discord.Forbidden:
            pass
        await self._log(after.id, embed)

    # ── Role Events ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        embed = log_embed("🎭  Role Created", C_ROLE)
        embed.add_field(name="🎭 Role",    value=f"{role.mention} `{role.name}`", inline=True)
        embed.add_field(name="🎨 Colour",  value=str(role.colour),                inline=True)
        embed.add_field(name="📌 Hoisted", value=str(role.hoist),                 inline=True)
        try:
            async for entry in role.guild.audit_logs(limit=3, action=discord.AuditLogAction.role_create):
                if entry.target.id == role.id:
                    embed.add_field(name="🛡️ Created by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                    break
        except discord.Forbidden:
            pass
        embed.set_footer(text=f"Role ID: {role.id}")
        await self._log(role.guild.id, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        embed = log_embed("🗑️  Role Deleted", C_MSG_DEL)
        embed.add_field(name="🎭 Role",    value=f"`{role.name}`", inline=True)
        embed.add_field(name="🎨 Colour",  value=str(role.colour), inline=True)
        try:
            async for entry in role.guild.audit_logs(limit=3, action=discord.AuditLogAction.role_delete):
                if entry.target.id == role.id:
                    embed.add_field(name="🛡️ Deleted by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                    break
        except discord.Forbidden:
            pass
        embed.set_footer(text=f"Role ID: {role.id}")
        await self._log(role.guild.id, embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if before.colour != after.colour:
            changes.append(f"**Colour:** `{before.colour}` → `{after.colour}`")
        if before.hoist != after.hoist:
            changes.append(f"**Hoisted:** `{before.hoist}` → `{after.hoist}`")
        if before.mentionable != after.mentionable:
            changes.append(f"**Mentionable:** `{before.mentionable}` → `{after.mentionable}`")
        if not changes:
            return
        embed = log_embed("🎭  Role Updated", C_ROLE)
        embed.add_field(name="🎭 Role",    value=f"{after.mention} `{after.name}`", inline=True)
        embed.add_field(name="📝 Changes", value="\n".join(changes),               inline=False)
        try:
            async for entry in before.guild.audit_logs(limit=3, action=discord.AuditLogAction.role_update):
                if entry.target.id == after.id:
                    embed.add_field(name="🛡️ Updated by", value=f"{entry.user.mention} `{entry.user}`", inline=True)
                    break
        except discord.Forbidden:
            pass
        embed.set_footer(text=f"Role ID: {after.id}")
        await self._log(before.guild.id, embed)

    # ── Invites ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        embed = log_embed("🔗  Invite Created", C_JOIN)
        embed.add_field(name="🔗 Code",    value=f"`{invite.code}`",                              inline=True)
        embed.add_field(name="📺 Channel", value=invite.channel.mention if invite.channel else "?", inline=True)
        embed.add_field(name="👤 Created by", value=f"{invite.inviter.mention} `{invite.inviter}`" if invite.inviter else "Unknown", inline=True)
        embed.add_field(name="⏳ Expires",  value=_ts(invite.expires_at) if invite.expires_at else "Never", inline=True)
        embed.add_field(name="🔢 Max Uses", value=str(invite.max_uses) if invite.max_uses else "Unlimited", inline=True)
        await self._log(invite.guild.id, embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        embed = log_embed("🔗  Invite Deleted", C_LEAVE)
        embed.add_field(name="🔗 Code",    value=f"`{invite.code}`",                               inline=True)
        embed.add_field(name="📺 Channel", value=invite.channel.mention if invite.channel else "?", inline=True)
        await self._log(invite.guild.id, embed)


async def setup(bot):
    await bot.add_cog(Logging(bot))
