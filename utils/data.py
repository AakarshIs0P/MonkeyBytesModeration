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
    "AntiNuke":     ("🛡️", "AntiNuke", "Protect against destructive moderator actions"),
    "AutoMod":      ("🟠", "AutoMod", "Spam and content moderation controls"),
    "Reversion":    ("⚡", "Reversion",          "Auto-reverts guild actions when antinuke fires"),
    "Tickets":      ("🎟️", "Support Tickets",    "Create and manage support tickets."),
    "Translation":  ("🌐", "Translator",       "Translate text into supported languages"),
    "CustomCommands": ("🧩", "Custom Commands", "Create, delete, and list server-made commands"),
    "Sticky":       ("📌", "Sticky Messages",  "Keep important messages refreshed in channels"),
    "Giveaway":     ("🎉", "Giveaways",       "Advanced giveaways with multi-winner & role locks"),
    "StockTrading": ("📈", "Stock Trading",    "Practice trading stocks with fake CredCoins and beginner examples"),
    "InviteTracker": ("🔗", "Invite Tracker", "Track valid invites, alts, leaves, and net totals"),
    "Essentials":   ("🧰", "Essentials", "Suggestions, rules, and server inspection tools"),
    "TTS":          ("🗣️", "Text to Speech", "Generate audio with Edge TTS voices"),
    # Internal cogs — no help entry
    "Events":       None,
    "Help":         None,
}

ACCENT_COLOUR = discord.Colour.from_str("#5865F2")


# Curated examples supplement the automatic examples generated from signatures.
# Commands not listed here still receive a meaningful syntax-based example.
COMMAND_GUIDES = {
    "help": ("!help ban", "Start here, then look up any command or category by name."),
    "kick": ("!kick @member Repeated rule violations", "Requires the Kick Members permission."),
    "ban": ("!ban @member Spam links", "Use !unban <user_id> to reverse a ban."),
    "timeout": ("!timeout @member 10m Please cool down", "Durations accept values such as 30s, 10m, 2h, or 1d."),
    "purge": ("!purge 25", "Only deletes messages in the current channel."),
    "warn": ("!warn @member Please keep chat civil", "Warnings can be viewed with !warnings @member."),
    "translate": ("!translate spanish Hello, how are you?", "You can also reply to a message and run the command."),
    "afk": ("!afk Eating dinner", "Your AFK status is cleared automatically when you speak again."),
    "reminder": ("!reminder 30m Stretch and drink water", "Use a duration such as 10m, 2h, or 1d."),
    "giveaway": ("!giveaway 1h 1 Nitro Classic", "Use !help giveaway for its subcommands."),
    "ticket": ("!ticket setup", "Use !help ticket for ticket setup and management commands."),
    "ask": ("!ask Explain recursion simply", "Best for one-off AI questions."),
    "chat": ("!chat Help me plan a study schedule", "Remembers recent context in this channel."),
    "price": ("!price AAPL", "Stock symbols use their standard ticker format."),
    "buy": ("!buy AAPL 5", "Check !balance before buying."),
    "invites": ("!invites @member", "Omit the member to view your own invite statistics."),
    "automodset": ("!automodset spam_count 6", "Run !automodset with no options to view all settings."),
    "paladinset": ("!paladinset threshold ban 2", "Run !paladinset to review every antinuke setting."),
    "whitelist": ("!whitelist @trusted_mod", "Whitelisted members bypass AntiNuke and AutoMod."),
    "suggest": ("!suggest Add a music channel", "Staff can set a dedicated channel with !suggestionset #channel."),
    "rules": ("!rules", "Server staff can create these with !setrules."),
    "roleinfo": ("!roleinfo @Moderators", "Shows the role's members, position, and permissions."),
    "channelinfo": ("!channelinfo #general", "Omit the channel to inspect the current channel."),
    "perms": ("!perms @member", "Shows effective permissions in the current channel."),
    "botperms": ("!botperms", "Use this when the bot cannot respond or react in a channel."),
    "tts": ("!tts en-US-JennyNeural Hello from MonkeyBytes", "Use !ttsvoices en-US to find every available voice."),
    "ttsvoices": ("!ttsvoices hi-IN", "Search the live Edge TTS catalog by name, locale, or gender."),
}


def _clean_command_text(value) -> str:
    """Turn command help/docstrings into a compact help-menu description."""
    return " ".join(str(value or "").strip().split())


def command_description(command) -> str:
    description = _clean_command_text(
        getattr(command, "help", None) or getattr(command, "description", None)
    )
    if description:
        return description
    return f"Run this command to use {command.name.replace('_', ' ')} features."


def command_usage(command, prefix: str = "!") -> str:
    signature = _clean_command_text(getattr(command, "signature", ""))
    return f"{prefix}{command.qualified_name}{f' {signature}' if signature else ''}"


def command_example(command, prefix: str = "!") -> tuple[str, str]:
    guide = COMMAND_GUIDES.get(command.name)
    if guide:
        example, tip = guide
        return example.replace("!", prefix, 1), tip

    usage = command_usage(command, prefix)
    # A signature is safer than guessing concrete IDs, roles, or channels. It is
    # still directly copyable and clearly shows which values the user supplies.
    return usage, "Replace <required> values and optionally include [optional] values."


def build_command_embed(bot, command, prefix: str = "!") -> discord.Embed:
    """Create the same beginner-friendly command page for prefix and slash help."""
    embed = discord.Embed(
        title=f"Command guide: {prefix}{command.qualified_name}",
        description=command_description(command),
        colour=ACCENT_COLOUR,
    )
    embed.add_field(name="How to use it", value=f"`{command_usage(command, prefix)}`", inline=False)

    example, tip = command_example(command, prefix)
    embed.add_field(name="Example", value=f"`{example}`", inline=False)
    embed.add_field(name="Tip", value=tip, inline=False)

    app_command = getattr(command, "app_command", None)
    if app_command:
        embed.add_field(name="Slash command", value=f"`/{app_command.qualified_name}`", inline=True)
    if command.aliases:
        embed.add_field(name="Also called", value=" ".join(f"`{alias}`" for alias in command.aliases), inline=True)
    if command.cog:
        meta = COG_META.get(type(command.cog).__name__)
        if meta:
            embed.add_field(name="Category", value=f"{meta[0]} {meta[1]}", inline=True)
    if isinstance(command, commands.Group):
        subcommands = [child for child in command.commands if not child.hidden]
        if subcommands:
            names = ", ".join(f"`{child.name}`" for child in subcommands[:20])
            embed.add_field(name="Subcommands", value=names, inline=False)
    embed.set_footer(text="<required> must be included. [optional] can be left out.")
    return embed


def build_category_embed(bot, cog, prefix: str = "!") -> discord.Embed:
    """Render every category consistently, with syntax and examples for each command."""
    meta = COG_META.get(type(cog).__name__)
    emoji, label, description = meta if meta else ("📁", type(cog).__name__, "Command category")
    commands_in_cog = sorted((command for command in cog.get_commands() if not command.hidden), key=lambda command: command.name)
    embed = discord.Embed(
        title=f"{emoji} {label}",
        description=(
            f"{description}\n\n"
            f"Choose a command below, then run `{prefix}help <command>` for a full guide."
        ),
        colour=ACCENT_COLOUR,
    )
    for command in commands_in_cog[:25]:
        example, _ = command_example(command, prefix)
        embed.add_field(
            name=f"`{command_usage(command, prefix)}`",
            value=f"{command_description(command)}\nExample: `{example}`",
            inline=False,
        )
    if len(commands_in_cog) > 25:
        embed.set_footer(text=f"Showing 25 of {len(commands_in_cog)} commands. Use {prefix}help <command> for any command.")
    else:
        embed.set_footer(text=f"{len(commands_in_cog)} command{'s' if len(commands_in_cog) != 1 else ''} in this category")
    return embed


# ── Help UI components ─────────────────────────────────────────────────────────

class CategorySelect(discord.ui.Select):
    def __init__(self, cogs, bot, invoker_id: int, *, page: int = 1, pages: int = 1, row: int = 0):
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
            placeholder=f"Choose a command category ({page}/{pages})...",
            min_values=1, max_values=1,
            options=options,
            row=row,
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

        cmds = [c for c in cog.get_commands() if not c.hidden]
        if not cmds:
            return await interaction.response.send_message(
                "There are no public commands in this category yet.", ephemeral=True
            )

        prefix = getattr(self.bot, "prefix", "!") or "!"
        await interaction.response.edit_message(embed=build_category_embed(self.bot, cog, prefix))


class HomeButton(discord.ui.Button):
    def __init__(self, cogs, bot, invoker_id: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="Overview", row=4)
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
        ("🔒 Security & Moderation", ["AntiNuke", "AutoMod", "Reversion", "Moderator", "Warns", "Logging", "Reporter"]),
        ("🎮 Fun & Engagement",      ["Fun_Commands", "Blackjack", "Giveaway", "StockTrading", "Extras"]),
        ("🔧 Utilities",             ["Essentials", "TTS", "Translation", "Encryption", "CustomCommands", "MsgStats", "Discord_Info", "Information"]),
        ("⚙️ Management",            ["Admin", "ButtonRoles", "Tickets", "InviteTracker", "AI"]),
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
        pages = [cogs[index:index + 25] for index in range(0, len(cogs), 25)]
        for index, page_cogs in enumerate(pages):
            self.add_item(CategorySelect(page_cogs, bot, invoker_id, page=index + 1, pages=len(pages), row=index))
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
        await ctx.send(embed=build_command_embed(ctx.bot, command, prefix))

    async def send_group_help(self, group):
        ctx = self.context
        prefix = ctx.clean_prefix or ctx.prefix or "!"
        await ctx.send(embed=build_command_embed(ctx.bot, group, prefix))

    async def send_cog_help(self, cog):
        ctx = self.context
        prefix = ctx.clean_prefix or ctx.prefix or "!"
        return await ctx.send(embed=build_category_embed(ctx.bot, cog, prefix))

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
