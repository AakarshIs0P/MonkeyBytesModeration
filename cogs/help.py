"""Slash-command entry point for the shared beginner-friendly help system."""

import discord
from discord import app_commands
from discord.ext import commands

from utils.data import (
    ACCENT_COLOUR,
    COG_META,
    DiscordBot,
    HelpView,
    _build_home_embed,
    build_category_embed,
    build_command_embed,
)


class Help(commands.Cog):
    def __init__(self, bot: DiscordBot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse commands, examples, and categories.")
    @app_commands.describe(command="A command or category name, such as ban or AutoMod")
    async def slash_help(self, interaction: discord.Interaction, command: str | None = None):
        bot = interaction.client
        prefix = getattr(bot, "prefix", "!") or "!"

        if command:
            query = command.strip().lower()
            cmd = bot.get_command(query)
            if cmd:
                return await interaction.response.send_message(
                    embed=build_command_embed(bot, cmd, prefix), ephemeral=True
                )

            cog = next(
                (item for item in bot.cogs.values() if type(item).__name__.lower() == query),
                None,
            )
            if cog:
                return await interaction.response.send_message(
                    embed=build_category_embed(bot, cog, prefix), ephemeral=True
                )

            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="Command not found",
                    description=(
                        f"I could not find `{command}`. Try `/help` to browse categories, "
                        f"or type `{prefix}help` in the server."
                    ),
                    colour=discord.Colour.red(),
                ),
                ephemeral=True,
            )

        visible_cogs = [
            cog for cog in bot.cogs.values()
            if COG_META.get(type(cog).__name__) is not None
        ]
        total_commands = sum(
            len([cmd for cmd in cog.get_commands() if not cmd.hidden])
            for cog in visible_cogs
        )
        embed = _build_home_embed(bot, interaction.user, visible_cogs, total_commands)
        view = HelpView(visible_cogs, bot, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Help(bot))
