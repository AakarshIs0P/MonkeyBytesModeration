"""AutoMod commands and message listener for the Paladin protection suite.

The shared protection state lives in :mod:`cogs.antinuke`, so AutoMod and
Antinuke continue to use the same whitelist, alert subscriptions, bypass keys,
and persisted configuration without duplicating state.
"""

import discord
from discord.ext import commands


class AutoMod(commands.Cog):
    """Member-facing spam and content moderation controls."""

    def __init__(self, bot):
        self.bot = bot
        # Used by Logging to avoid duplicating AutoMod kick/ban entries.
        self.automod_acted: dict[int, set[int]] = {}

    @property
    def security(self):
        return self.bot.get_cog("AntiNuke")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if self.security:
            await self.security.on_message(message)

    @commands.command(name="automodstart", description="Toggle Paladin AutoMod spam protection on/off.")
    @commands.guild_only()
    async def automodstart(self, ctx: commands.Context):
        if self.security:
            await self.security.automodstart(ctx)

    @commands.command(name="automodset", description="Configure Paladin AutoMod settings.")
    @commands.guild_only()
    async def automodset(self, ctx: commands.Context, setting: str = None, *, value: str = None):
        if self.security:
            await self.security.automodset(ctx, setting, value=value)

    @commands.command(name="automodclear", description="Clear all automod strikes for a specific user.")
    @commands.guild_only()
    async def automodclear(self, ctx: commands.Context, member: discord.Member):
        if self.security:
            await self.security.automodclear(ctx, member)

    @commands.command(name="automodstrikes", description="View a user's current AutoMod strike count.")
    @commands.guild_only()
    async def automodstrikes(self, ctx: commands.Context, member: discord.Member):
        if self.security:
            await self.security.automodstrikes(ctx, member)


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
