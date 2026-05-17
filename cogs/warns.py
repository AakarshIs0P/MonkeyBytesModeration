import discord
import json
import os

from datetime import datetime, timezone
from discord.ext import commands, tasks
from discord import app_commands
from utils.default import CustomContext
from utils.data import DiscordBot
from utils import permissions
import asyncio

WARNS_FILE = "data/warns.json"
_warns_cache = {}
_warns_dirty = False
_warns_lock = asyncio.Lock()

def _ensure_data_dir():
    if not os.path.exists("data"):
        os.makedirs("data")

async def _add_warn(guild_id: str, user_id: str, reason: str, moderator: str, moderator_id: int):
    global _warns_dirty
    async with _warns_lock:
        if guild_id not in _warns_cache:
            _warns_cache[guild_id] = {}
        if user_id not in _warns_cache[guild_id]:
            _warns_cache[guild_id][user_id] = []
        _warns_cache[guild_id][user_id].append({
            "reason": reason,
            "moderator": moderator,
            "moderator_id": moderator_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        _warns_dirty = True
        return len(_warns_cache[guild_id][user_id])


def _warn_embed(member, moderator, reason, count):
    embed = discord.Embed(title="⚠️  Member Warned", colour=discord.Colour.orange())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Member",         value=f"{member.mention}\n`{member}`", inline=True)
    embed.add_field(name="🛡️ Moderator",      value=moderator.mention,              inline=True)
    embed.add_field(name="📊 Total Warnings", value=f"**{count}**",                 inline=True)
    embed.add_field(name="📝 Reason",         value=reason,                         inline=False)
    embed.set_footer(text=f"User ID: {member.id}")
    return embed


def _warnings_embed(member, guild_id):
    warns = _warns_cache.get(guild_id, {}).get(str(member.id), [])
    embed = discord.Embed(
        title=f"📋  Warnings — {member.display_name}",
        colour=discord.Colour.orange() if warns else discord.Colour.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if not warns:
        embed.description = f"✅ **{member.display_name}** has no warnings."
    else:
        embed.description = f"**{len(warns)}** warning{'s' if len(warns) != 1 else ''} on record."
        for i, w in enumerate(warns, 1):
            try:
                unix = int(datetime.fromisoformat(w.get("timestamp", "")).timestamp())
                time_str = f"<t:{unix}:R>"
            except Exception:
                time_str = "Unknown time"
            embed.add_field(name=f"Warning #{i}",
                value=f"**Reason:** {w['reason']}\n**By:** {w['moderator']}\n**When:** {time_str}", inline=False)
    embed.set_footer(text=f"User ID: {member.id}")
    return embed


class Warns(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot
        _ensure_data_dir()
        try:
            with open(WARNS_FILE, "r") as f:
                global _warns_cache
                _warns_cache = json.load(f)
        except Exception:
            _warns_cache = {}
        self.save_warns_loop.start()

    def cog_unload(self):
        self.save_warns_loop.cancel()
        if _warns_dirty:
            with open(WARNS_FILE, "w") as f:
                json.dump(_warns_cache, f, indent=2)

    @tasks.loop(seconds=60)
    async def save_warns_loop(self):
        global _warns_dirty
        async with _warns_lock:
            if _warns_dirty:
                tmp = WARNS_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(_warns_cache, f, indent=2)
                os.replace(tmp, WARNS_FILE)
                _warns_dirty = False

    # ── Warn ──────────────────────────────────────────────────────────

    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(kick_members=True)
    async def warn(self, ctx: CustomContext, member: discord.Member, *, reason: str = "No reason provided"):
        """ Warn a member. """
        if await permissions.check_priv(ctx, member):
            return
        count = await _add_warn(str(ctx.guild.id), str(member.id), reason, str(ctx.author), ctx.author.id)
        await ctx.send(embed=_warn_embed(member, ctx.author, reason, count))
        try:
            dm = discord.Embed(title=f"⚠️  You were warned in {ctx.guild.name}",
                description=f"**Reason:** {reason}", colour=discord.Colour.orange())
            dm.add_field(name="Total Warnings", value=str(count))
            dm.set_footer(text="Please follow the server rules.")
            await member.send(embed=dm)
        except discord.Forbidden:
            pass



    # ── Warnings ──────────────────────────────────────────────────────

    @commands.command(aliases=["infractions"])
    @commands.guild_only()
    @permissions.has_permissions(kick_members=True)
    async def warnings(self, ctx: CustomContext, member: discord.Member = None):
        """ View warnings for a member. """
        await ctx.send(embed=_warnings_embed(member or ctx.author, str(ctx.guild.id)))



    # ── Clear Warn ────────────────────────────────────────────────────

    @commands.command(aliases=["clearwarnings", "resetwarn"])
    @commands.guild_only()
    @permissions.has_permissions(kick_members=True)
    async def clearwarn(self, ctx: CustomContext, member: discord.Member, index: int = None):
        """ Clear all warnings or a specific one (by number) for a member. """
        global _warns_dirty
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        
        async with _warns_lock:
            warns = _warns_cache.get(guild_id, {}).get(user_id, [])
            if not warns:
                return await ctx.send(embed=discord.Embed(description=f"✅ **{member.display_name}** has no warnings.", colour=discord.Colour.green()))
            if index is not None:
                if index < 1 or index > len(warns):
                    return await ctx.send(embed=discord.Embed(description=f"❌ Invalid number. They have **{len(warns)}** warning(s).", colour=discord.Colour.red()))
                removed = warns.pop(index - 1)
                _warns_cache[guild_id][user_id] = warns
                _warns_dirty = True
                embed = discord.Embed(title="🗑️  Warning Removed", colour=discord.Colour.green())
                embed.add_field(name="Member",     value=member.mention,    inline=True)
                embed.add_field(name="Removed #",  value=str(index),        inline=True)
                embed.add_field(name="Reason was", value=removed["reason"], inline=False)
            else:
                count = len(warns)
                _warns_cache[guild_id][user_id] = []
                _warns_dirty = True
                embed = discord.Embed(title="🗑️  All Warnings Cleared",
                    description=f"Removed **{count}** warning{'s' if count != 1 else ''} from {member.mention}.",
                    colour=discord.Colour.green())
        embed.set_footer(text=f"Cleared by {ctx.author} • User ID: {member.id}")
        await ctx.send(embed=embed)




async def setup(bot):
    await bot.add_cog(Warns(bot))
