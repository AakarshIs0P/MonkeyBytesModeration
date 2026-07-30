"""Essential community and server-inspection commands.

This cog deliberately fills gaps between moderation, server information, and
community workflows without duplicating their existing commands.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.data import DiscordBot
from utils.json_store import AtomicJSONStore


SETTINGS_FILE = "data/essentials.json"
COLOUR = discord.Colour.blurple()


def _error(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", colour=discord.Colour.red())


def _permission_names(permission_source) -> list[str]:
    return [name.replace("_", " ").title() for name, enabled in permission_source if enabled]


class Essentials(commands.Cog):
    """Community workflows and clear server diagnostics."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self.store = AtomicJSONStore(SETTINGS_FILE, dict)

    async def cog_load(self):
        await self.store.load()

    async def cog_unload(self):
        await self.store.flush()

    def _settings(self, guild_id: int) -> dict:
        return self.store.data.setdefault(str(guild_id), {"suggestion_channel": None, "rules": ""})

    async def _save_settings(self) -> None:
        self.store.mark_dirty()
        await self.store.flush()

    # ------------------------------------------------------------------
    # Suggestions
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="suggest", description="Send a suggestion for this server.")
    @commands.guild_only()
    @app_commands.describe(suggestion="Your suggestion")
    async def suggest(self, ctx: commands.Context, *, suggestion: str):
        """Submit a suggestion for members to vote on."""
        suggestion = suggestion.strip()
        if not suggestion:
            return await ctx.send(embed=_error("Please include a suggestion."), ephemeral=True)
        if len(suggestion) > 1_500:
            return await ctx.send(embed=_error("Suggestions must be 1,500 characters or fewer."), ephemeral=True)

        settings = self._settings(ctx.guild.id)
        channel_id = settings.get("suggestion_channel")
        channel = self.bot.get_channel(channel_id) if channel_id else ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send(embed=_error("The configured suggestion channel no longer exists."), ephemeral=True)
        if not channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.send(embed=_error(f"I cannot send messages in {channel.mention}."), ephemeral=True)

        embed = discord.Embed(title="💡 New Suggestion", description=suggestion, colour=COLOUR)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Suggested by {ctx.author} • Vote with the reactions below")
        embed.timestamp = datetime.now(timezone.utc)
        message = await channel.send(embed=embed)
        for emoji in ("✅", "❌"):
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                pass

        if channel.id != ctx.channel.id:
            await ctx.send(f"✅ Your suggestion was posted in {channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="suggestionset", description="Set the channel used for suggestions.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.describe(channel="Suggestion channel; omit to use the current channel")
    async def suggestionset(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """Choose where server suggestions are posted."""
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send(embed=_error("Choose a text channel."), ephemeral=True)
        self._settings(ctx.guild.id)["suggestion_channel"] = channel.id
        await self._save_settings()
        await ctx.send(f"✅ Suggestions will now be posted in {channel.mention}.", ephemeral=True)

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="rules", description="Show this server's rules.")
    @commands.guild_only()
    async def rules(self, ctx: commands.Context):
        """View the rules configured by this server's staff."""
        rules = self._settings(ctx.guild.id).get("rules", "").strip()
        if not rules:
            return await ctx.send(embed=_error("This server has not configured rules yet."), ephemeral=True)
        embed = discord.Embed(title=f"📜 {ctx.guild.name} Rules", description=rules, colour=COLOUR)
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="setrules", description="Create or update this server's rules.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.describe(rules_text="Rules to display; use new lines to separate them")
    async def setrules(self, ctx: commands.Context, *, rules_text: str):
        """Set the rules shown by !rules."""
        rules_text = rules_text.strip()
        if not rules_text:
            return await ctx.send(embed=_error("Rules cannot be empty."), ephemeral=True)
        if len(rules_text) > 4_000:
            return await ctx.send(embed=_error("Rules must be 4,000 characters or fewer."), ephemeral=True)
        self._settings(ctx.guild.id)["rules"] = rules_text
        await self._save_settings()
        await ctx.send("✅ Server rules updated. Members can now use `!rules`.", ephemeral=True)

    # ------------------------------------------------------------------
    # Inspection tools
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="roleinfo", description="Show details for a server role.")
    @commands.guild_only()
    @app_commands.describe(role="The role to inspect")
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        """Inspect a role, including its permissions and member count."""
        members = sum(role in member.roles for member in ctx.guild.members)
        permissions = _permission_names(role.permissions)
        permission_text = ", ".join(permissions[:20]) or "No permissions"
        if len(permissions) > 20:
            permission_text += f" … and {len(permissions) - 20} more"
        embed = discord.Embed(title=f"🏷️ Role: {role.name}", colour=role.colour if role.colour.value else COLOUR)
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Members", value=str(members), inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Managed", value="Yes" if role.managed else "No", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(role.created_at, "R"), inline=True)
        embed.add_field(name="Permissions", value=permission_text, inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="channelinfo", description="Show details for a channel.")
    @commands.guild_only()
    @app_commands.describe(channel="The channel to inspect; defaults to the current channel")
    async def channelinfo(self, ctx: commands.Context, channel: discord.abc.GuildChannel | None = None):
        """Inspect a text, voice, category, forum, or stage channel."""
        channel = channel or ctx.channel
        embed = discord.Embed(title=f"# Channel: {channel.name}", colour=COLOUR)
        embed.add_field(name="ID", value=f"`{channel.id}`", inline=True)
        embed.add_field(name="Type", value=channel.__class__.__name__.replace("Channel", "") or "Channel", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(channel.created_at, "R"), inline=True)
        if getattr(channel, "category", None):
            embed.add_field(name="Category", value=channel.category.mention, inline=True)
        if isinstance(channel, discord.TextChannel):
            embed.add_field(name="Topic", value=channel.topic or "No topic", inline=False)
            embed.add_field(name="Slowmode", value=f"{channel.slowmode_delay}s", inline=True)
            embed.add_field(name="NSFW", value="Yes" if channel.is_nsfw() else "No", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="perms", aliases=["permissions"], description="Show a member's permissions in this channel.")
    @commands.guild_only()
    @app_commands.describe(member="Member to check; defaults to you")
    async def perms(self, ctx: commands.Context, member: discord.Member | None = None):
        """Check effective channel permissions for a member."""
        member = member or ctx.author
        permissions = _permission_names(ctx.channel.permissions_for(member))
        shown = ", ".join(permissions[:30]) or "No allowed permissions"
        if len(permissions) > 30:
            shown += f" … and {len(permissions) - 30} more"
        embed = discord.Embed(title=f"🔐 Permissions: {member.display_name}", colour=COLOUR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = f"Effective permissions in {ctx.channel.mention}."
        embed.add_field(name="Allowed", value=shown, inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="botperms", description="Check the bot's permissions in this channel.")
    @commands.guild_only()
    async def botperms(self, ctx: commands.Context):
        """Diagnose whether the bot can operate in the current channel."""
        permissions = ctx.channel.permissions_for(ctx.guild.me)
        checks = {
            "View Channel": permissions.view_channel,
            "Send Messages": permissions.send_messages,
            "Embed Links": permissions.embed_links,
            "Read History": permissions.read_message_history,
            "Add Reactions": permissions.add_reactions,
            "Manage Messages": permissions.manage_messages,
            "Attach Files": permissions.attach_files,
        }
        status = "\n".join(f"{'✅' if allowed else '❌'} {name}" for name, allowed in checks.items())
        embed = discord.Embed(title="🤖 Bot Permission Check", description=f"For {ctx.channel.mention}", colour=COLOUR)
        embed.add_field(name="Capabilities", value=status, inline=False)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Essentials(bot))
