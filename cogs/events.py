import discord
import psutil
import os

from datetime import datetime
from utils.default import CustomContext
from discord.ext import commands
from discord.ext.commands import errors
from discord import app_commands
from utils import default
from utils.data import DiscordBot


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot
        self.process = psutil.Process(os.getpid())

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = str(error) if str(error) else "❌ You don't have permission to use this command."
        embed = discord.Embed(description=msg, colour=discord.Colour.red())
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_command_error(self, ctx: CustomContext, err: Exception):
        # Hybrid commands invoked via slash also raise on_app_command_error.
        # Skip here to prevent the user receiving two identical error messages.
        if getattr(ctx, "interaction", None) is not None:
            return
        if isinstance(err, errors.MissingRequiredArgument) or isinstance(err, errors.BadArgument):
            signature = f"{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}"
            await ctx.send(f"❌ **Invalid Usage.**\nUse: `{signature}`", ephemeral=True)
        elif isinstance(err, errors.CommandInvokeError):
            if "2000 or fewer" in str(err) and len(ctx.message.clean_content) > 1900:
                return await ctx.send("⚠️ Output was too long to display.")
            # Log the full traceback privately — never send it to the channel
            import logging as _logging
            _logging.getLogger("bot.events").error(
                "CommandInvokeError in %s: %s",
                ctx.command,
                default.traceback_maker(err.original),
            )
            await ctx.send("⚠️ Something went wrong. Please try again.")
        elif isinstance(err, errors.CheckFailure):
            msg = str(err) or "You don't have permission to use this command."
            await ctx.send(f"⚠️ {msg}")
        elif isinstance(err, errors.MaxConcurrencyReached):
            await ctx.send("⚠️ You already have a command running. Please wait.")
        elif isinstance(err, errors.CommandOnCooldown):
            ready_at = int(datetime.now().timestamp() + err.retry_after)
            await ctx.send(f"⏳ Cooldown. Try again <t:{ready_at}:R>.")
        elif isinstance(err, errors.CommandNotFound):
            pass

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        to_send = next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if to_send:
            await to_send.send(self.bot.config.discord_join_message)

    @commands.Cog.listener()
    async def on_command(self, ctx: CustomContext):
        location_name = ctx.guild.name if ctx.guild else "Private message"
        print(f"{location_name} > {ctx.author} > {ctx.message.clean_content}")

    @commands.Cog.listener()
    async def on_ready(self):
        if not hasattr(self.bot, "uptime"):
            self.bot.uptime = datetime.now()
        status_type = {"idle": discord.Status.idle, "dnd": discord.Status.dnd}
        activity_type = {"listening": 2, "watching": 3, "competing": 5}
        await self.bot.change_presence(
            activity=discord.Activity(
                type=activity_type.get(self.bot.config.discord_activity_type.lower(), 0),
                name=self.bot.config.discord_activity_name
            ),
            status=status_type.get(self.bot.config.discord_status_type.lower(), discord.Status.online)
        )
        print(f"✅ Ready: {self.bot.user} | Servers: {len(self.bot.guilds)}")


async def setup(bot):
    await bot.add_cog(Events(bot))
