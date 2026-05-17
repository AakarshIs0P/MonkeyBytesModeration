import discord

from discord.ext import commands
from discord import app_commands
from utils.data import DiscordBot, COG_META, ACCENT_COLOUR, _build_home_embed, HelpView, _get_cog_help_embed


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot

    @app_commands.command(name="help", description="Browse all bot commands.")
    @app_commands.describe(command="Specific command to look up (optional)")
    async def slash_help(self, interaction: discord.Interaction, command: str = None):
        bot = interaction.client

        if command:
            cmd = bot.get_command(command)
            if not cmd:
                cog = bot.get_cog(command)
                if cog:
                    prefix = getattr(bot, "prefix", "!") or "!"
                    if hasattr(cog, "help_embed"):
                        embed = _get_cog_help_embed(cog, prefix=prefix, guild=interaction.guild)
                    else:
                        meta = COG_META.get(type(cog).__name__)
                        emoji, label, desc = meta if meta else ("📁", type(cog).__name__, "")
                        embed = discord.Embed(
                            title=f"{emoji} {label}",
                            description=desc or "No description provided.",
                            colour=ACCENT_COLOUR,
                        )
                        for cog_command in [c for c in cog.get_commands() if not c.hidden]:
                            embed.add_field(
                                name=f"{prefix}{cog_command.name}",
                                value=cog_command.help or "No description provided.",
                                inline=False,
                            )
                    return await interaction.response.send_message(embed=embed, ephemeral=True)
            if not cmd:
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌  Not Found",
                        description=f"No command named `{command}` found.",
                        colour=discord.Colour.red()
                    ),
                    ephemeral=True
                )
            prefix = getattr(bot, "prefix", "!") or "!"
            embed = discord.Embed(
                title=f"📖  `{cmd.name}`",
                description=cmd.help or "No description provided.",
                colour=ACCENT_COLOUR
            )
            embed.add_field(name="Prefix Usage", value=f"`{prefix}{cmd.qualified_name}" + (f" {cmd.signature}`" if cmd.signature else "`"), inline=True)
            embed.add_field(name="Slash Usage",  value=f"`/{cmd.name}`", inline=True)
            if cmd.aliases:
                embed.add_field(name="Aliases", value="  ".join(f"`{a}`" for a in cmd.aliases), inline=False)
            if cmd.cog:
                meta = COG_META.get(type(cmd.cog).__name__)
                if meta:
                    embed.add_field(name="Category", value=f"{meta[0]}  {meta[1]}", inline=True)
            embed.set_footer(text="<required>   [optional]", icon_url=bot.user.display_avatar.url)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        visible_cogs = [cog for cog in bot.cogs.values() if COG_META.get(type(cog).__name__) is not None]
        total_cmds = sum(len([c for c in cog.get_commands() if not c.hidden]) for cog in visible_cogs)
        embed = _build_home_embed(bot, interaction.user, visible_cogs, total_cmds)
        view = HelpView(visible_cogs, bot, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Help(bot))
