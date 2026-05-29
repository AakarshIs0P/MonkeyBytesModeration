"""
utils/data.py

Contains:
  - COG_META: mapping cog CLASS NAMES → (emoji, label, description)
  - HelpView, HelpFormat: full interactive help system
  - DiscordBot: main bot class with cog loader
"""

import discord
import os
import logging

from discord.ext import commands
from discord.ext.commands import AutoShardedBot
from utils import permissions, default
from utils.config import Config

log = logging.getLogger("bot.data")

# ── COG_META keys MUST match the Cog class name exactly ───────────────────────
# NOTE: MsgStats cog has name="MsgStats" in its class definition (matching this key)
COG_META = {
    "Fun_Commands": ("🎮", "Fun & Games",    "Games, memes, and silly commands"),
    "Extras":       ("⭐", "Extras",          "Snipe, polls, reminders, AFK & more"),
    "Information":  ("📊", "Information",    "Bot stats, ping, invites and more"),
    "Discord_Info": ("🔍", "Server & Users", "Server info, user profiles, avatars"),
    "MsgStats":     ("💬", "Message Stats",  "First message, leaderboard, message counts"),
    "Blackjack":    ("🃏", "Blackjack",      "Play blackjack with optional betting"),
    "Moderator":    ("🛡️", "Moderation",     "Kick, ban, mute, timeout, prune and more"),
    "Reporter":     ("📋", "Reports",        "DM-based user report system with evidence"),
    "Warns":        ("⚠️", "Warnings",       "Warn system — warn, view, clear"),
    "Logging":      ("📋", "Logging",        "Server event & mod action logging"),
    "Encryption":   ("🔐", "Encryption",     "Encode and decode text in many formats"),
    "Admin":        ("⚙️", "Admin",          "Bot management: announce, dm, reload and more"),
    "ButtonRoles":  ("🎭", "Button Roles",   "Send persistent role-picker buttons to a channel"),
    "AI":           ("🤖", "AI Chat",            "Ask questions or chat with a Groq-powered LLM"),
    "Paladin":      ("🛡️", "Paladin Protection", "Antinuke & AutoMod — server security system"),
    "Reversion":    ("⚡", "Reversion",          "Auto-reverts guild actions when antinuke fires"),
    "Tickets":      ("🎟️", "Support Tickets",    "Create and manage support tickets."),
    "Translation":  ("🌐", "Translator",       "Translate text into supported languages"),
    "CustomCommands": ("🧩", "Custom Commands", "Create, delete, and list server-made commands"),
    "Sticky":       ("📌", "Sticky Messages",  "Keep important messages refreshed in channels"),
    "Giveaway":     ("🎉", "Giveaways",       "Advanced giveaways with multi-winner & role locks"),
    "StockTrading": ("📈", "Stock Trading",    "Practice trading stocks with fake CredCoins and beginner examples"),
    # Internal cogs — no help entry
    "Events":       None,
    "Help":         None,
}

ACCENT_COLOUR = discord.Colour.from_str("#5865F2")


def _get_cog_help_embed(cog, prefix: str, guild=None) -> discord.Embed:
    try:
        return cog.help_embed(prefix=prefix, guild=guild)
    except TypeError:
        return cog.help_embed(prefix=prefix)


# ── Help UI components ─────────────────────────────────────────────────────────

class CategorySelect(discord.ui.Select):
    def __init__(self, cogs, bot, invoker_id: int):
        self.bot = bot
        self.invoker_id = invoker_id
        options = []
        for cog in cogs:
            meta = COG_META.get(type(cog).__name__)
            if not meta:
                continue
            emoji, label, desc = meta
            options.append(discord.SelectOption(
                label=label, description=desc, emoji=emoji, value=type(cog).__name__
            ))
        super().__init__(
            placeholder="Choose a command category...",
            min_values=1, max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message(
                "You cannot interact with this menu.",
                ephemeral=True,
            )
        cog_name = self.values[0]
        cog = self.bot.cogs.get(cog_name)
        if not cog:
            return await interaction.response.send_message("That category could not be found.", ephemeral=True)

        meta = COG_META.get(cog_name)
        emoji, label, desc = meta

        # Use custom help embed if the cog provides one
        if hasattr(cog, "help_embed"):
            prefix = getattr(self.bot, "prefix", "!") or "!"
            return await interaction.response.edit_message(
                embed=_get_cog_help_embed(cog, prefix=prefix, guild=interaction.guild)
            )

        cmds = [c for c in cog.get_commands() if not c.hidden]
        if not cmds:
            return await interaction.response.send_message(
                "There are no public commands in this category yet.", ephemeral=True
            )

        embed = discord.Embed(
            title=f"{emoji} {label}",
            description=f"{desc}\n\nUse `{getattr(self.bot, 'prefix', '!') or '!'}help <command>` for one command.",
            colour=ACCENT_COLOUR,
        )
        for cmd in cmds:
            aliases = f" (Aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
            value = cmd.help or "No description has been added yet."
            if hasattr(cmd, "commands"):
                sub_names = ", ".join(f"`{s.name}`" for s in cmd.commands)
                value += f"\nSubcommands: {sub_names}"
            prefix = getattr(self.bot, "prefix", "!") or "!"
            usage = f"`{prefix}{cmd.name}{f' {cmd.signature}' if cmd.signature else ''}`"
            embed.add_field(name=f"{usage}{aliases}", value=value, inline=False)
        embed.set_footer(
            text=f"{len(cmds)} command{'s' if len(cmds) != 1 else ''} in this category"
        )
        await interaction.response.edit_message(embed=embed)


class HomeButton(discord.ui.Button):
    def __init__(self, cogs, bot, invoker_id: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="Overview", row=1)
        self.cogs = cogs
        self.bot = bot
        self.invoker_id = invoker_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message(
                "You cannot interact with this menu.", ephemeral=True
            )
        visible = [c for c in self.cogs if COG_META.get(type(c).__name__) is not None]
        total = sum(len([cmd for cmd in cog.get_commands() if not cmd.hidden]) for cog in visible)
        embed = _build_home_embed(self.bot, interaction.user, visible, total)
        await interaction.response.edit_message(embed=embed)


def _build_home_embed(bot, author, visible_cogs: list, total_cmds: int) -> discord.Embed:
    prefix = getattr(bot, "prefix", "!") or "!"
    embed = discord.Embed(
        title=f"📖  {bot.user.name} — Command Center",
        description=(
            f"Welcome, **{author.display_name}**. Pick a category below to browse "
            f"**{total_cmds}** commands across **{len(visible_cogs)}** categories.\n"
            f"For a single command, type `{prefix}help <command>` like `{prefix}help ban`.\n"
            f"\u200b"
        ),
        colour=ACCENT_COLOUR,
    )
    if bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    # Group cogs into sections for a cleaner look
    SECTION_ORDER = [
        ("🔒 Security & Moderation", ["Paladin", "Reversion", "Moderator", "Warns", "Logging", "Reporter"]),
        ("🎮 Fun & Engagement",      ["Fun_Commands", "Blackjack", "Giveaway", "StockTrading", "Extras"]),
        ("🔧 Utilities",             ["Translation", "Encryption", "CustomCommands", "MsgStats", "Discord_Info", "Information"]),
        ("⚙️ Management",            ["Admin", "ButtonRoles", "Tickets", "AI"]),
    ]

    cog_map = {type(c).__name__: c for c in visible_cogs}

    for section_title, cog_names in SECTION_ORDER:
        lines = []
        for name in cog_names:
            if name not in cog_map:
                continue
            meta = COG_META.get(name)
            if not meta:
                continue
            emoji, label, desc = meta
            cmd_count = len([c for c in cog_map[name].get_commands() if not c.hidden])
            lines.append(f"{emoji} **{label}** — {desc} (`{cmd_count}`)")
        if lines:
            embed.add_field(
                name=section_title,
                value="\n".join(lines),
                inline=False,
            )

    embed.add_field(
        name="💡 Quick Tips",
        value=(
            f"`{prefix}help StockTrading` - Beginner stock trading guide\n"
            f"`{prefix}help <command>` - Details for one command\n"
            f"`/help` - Slash command version\n"
            f"`{prefix}listcc` - View this server's custom commands"
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Requested by {author} - Use the dropdown to browse categories",
        icon_url=author.display_avatar.url,
    )
    return embed


class HelpView(discord.ui.View):
    def __init__(self, cogs, bot, invoker_id: int):
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.message = None
        self.add_item(CategorySelect(cogs, bot, invoker_id))
        self.add_item(HomeButton(cogs, bot, invoker_id))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True


class HelpFormat(commands.HelpCommand):
    """Clean and minimal prefix help command."""

    def get_destination(self):
        return self.context.channel

    async def send_bot_help(self, mapping):
        ctx = self.context
        bot = ctx.bot
        visible_cogs = [
            cog for cog in bot.cogs.values()
            if COG_META.get(type(cog).__name__) is not None
        ]
        total_cmds = sum(
            len([c for c in cog.get_commands() if not c.hidden]) for cog in visible_cogs
        )
        embed = _build_home_embed(bot, ctx.author, visible_cogs, total_cmds)
        view = HelpView(visible_cogs, bot, ctx.author.id)
        view.message = await ctx.send(embed=embed, view=view)

    async def send_command_help(self, command):
        ctx = self.context
        prefix = ctx.clean_prefix or ctx.prefix or "!"
        embed = discord.Embed(
            title=f"Help: {prefix}{command.qualified_name}",
            description=command.help or "No description has been added yet.",
            colour=ACCENT_COLOUR,
        )
        usage = f"{prefix}{command.qualified_name}{f' {command.signature}' if command.signature else ''}"
        
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
        embed.add_field(name="Slash Command", value=f"`/{command.name}` if available", inline=True)
        
        if command.aliases:
            embed.add_field(
                name="Aliases",
                value=", ".join(f"`{a}`" for a in command.aliases),
                inline=True,
            )
        if command.cog:
            meta = COG_META.get(type(command.cog).__name__)
            if meta:
                embed.add_field(name="Category", value=f"{meta[0]} {meta[1]}", inline=True)
        embed.set_footer(text="<required> means you must include it. [optional] means you can leave it out.")
                
        await ctx.send(embed=embed)

    async def send_group_help(self, group):
        ctx = self.context
        embed = discord.Embed(
            title=group.name,
            description=group.help or "No description provided.",
            colour=ACCENT_COLOUR,
        )
        if group.aliases:
            embed.add_field(
                name="Aliases",
                value=", ".join(f"`{a}`" for a in group.aliases),
                inline=False,
            )
        subs = [c for c in group.commands if not c.hidden]
        if subs:
            embed.add_field(
                name="Subcommands",
                value="\n".join(
                    f"`{ctx.prefix}{group.name} {s.name}` - {s.help or 'No description.'}"
                    for s in subs
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    async def send_cog_help(self, cog):
        ctx = self.context
        if hasattr(cog, "help_embed"):
            return await ctx.send(
                embed=_get_cog_help_embed(cog, prefix=ctx.clean_prefix or ctx.prefix, guild=ctx.guild)
            )
        meta = COG_META.get(type(cog).__name__)
        emoji, label, desc = meta if meta else ("📁", type(cog).__name__, "")
        cmds = [c for c in cog.get_commands() if not c.hidden]
        embed = discord.Embed(title=f"{emoji} {label}", description=desc, colour=ACCENT_COLOUR)
        for cmd in cmds:
            embed.add_field(name=cmd.name, value=cmd.help or "No description.", inline=False)
        await ctx.send(embed=embed)

    async def send_error_message(self, error):
        embed = discord.Embed(
            title="Command Not Found", description=error, colour=discord.Colour.red()
        )
        await self.context.send(embed=embed)


# ── Bot class ──────────────────────────────────────────────────────────────────

# Cogs loaded in this order (rest loaded alphabetically after)
_PRIORITY_COGS = ["msg_stats"]

# Cogs excluded from auto-loading (internal or special-purpose)
_SKIP_COGS = {"discord"}  # 'discord' shadows the discord library


class DiscordBot(AutoShardedBot):
    def __init__(self, config: Config, prefix: str = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = prefix
        self.config = config

    async def setup_hook(self):
        """Load all cogs and sync slash commands."""
        loaded = set()

        # Priority cogs first
        for name in _PRIORITY_COGS:
            try:
                await self.load_extension(f"cogs.{name}")
                loaded.add(name)
                log.info(f"Loaded cog: cogs.{name}")
            except Exception as e:
                log.error(f"Failed to load priority cog {name}: {e}", exc_info=True)

        # Load remaining cogs
        cogs_dir = os.path.join(os.path.dirname(__file__), "..", "cogs")
        for file in sorted(os.listdir(cogs_dir)):
            if not file.endswith(".py"):
                continue
            name = file[:-3]
            if name in _SKIP_COGS or name in loaded:
                continue
            try:
                await self.load_extension(f"cogs.{name}")
                loaded.add(name)
                log.info(f"Loaded cog: cogs.{name}")
            except Exception as e:
                log.error(f"Failed to load cog {name}: {e}", exc_info=True)

        # Sync slash commands globally
        try:
            synced = await self.tree.sync()
            log.info(f"Slash commands synced: {len(synced)} commands")
        except Exception as e:
            log.error(f"Failed to sync slash commands: {e}", exc_info=True)

    async def on_message(self, msg: discord.Message):
        if not self.is_ready() or msg.author.bot:
            return
        if not permissions.can_handle(msg, "send_messages"):
            return
        await self.process_commands(msg)

    async def process_commands(self, msg: discord.Message):
        ctx = await self.get_context(msg, cls=default.CustomContext)
        await self.invoke(ctx)
