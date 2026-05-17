"""
cogs/buttonroles.py

Button Roles — send an embed + persistent role-toggle buttons to a channel.

Prefix:  !br #channel emoji1 @role1 emoji2 @role2 ... (up to 15 pairs)
Slash:   /br channel:#channel pairs:"emoji1 role_id emoji2 role_id ..."

Buttons survive bot restarts (custom_id encodes the role ID).
Anyone can click a button to get or remove that role from themselves.
An embed above the buttons explains which button grants which role.
"""

import discord
import json
import os
import re
import asyncio

from discord.ext import commands
from discord import app_commands
from utils import permissions
from utils.default import CustomContext
from utils.data import DiscordBot, ACCENT_COLOUR

COL_SUCCESS = discord.Colour.green()
COL_ERROR   = discord.Colour.red()
COL_INFO    = ACCENT_COLOUR

MAX_PAIRS = 15

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "buttonroles.json")


# ── Data helpers ───────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    def _do_save():
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, DATA_FILE)
    return _do_save()


# ── Emoji & role token helpers ─────────────────────────────────────────────────

# Matches Discord custom emoji: <:name:id> or <a:name:id>
_CUSTOM_EMOJI_RE = re.compile(r"^<a?:[A-Za-z0-9_]+:\d+>$")

# Matches a role mention: <@&id>
_ROLE_MENTION_RE = re.compile(r"^<@&(\d{17,20})>$")

# Matches a bare role/user snowflake ID
_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")

# Broad unicode emoji range (covers the vast majority of standard emoji)
_UNICODE_EMOJI_RE = re.compile(
    r"^[\U0001F000-\U0001FFFF"
    r"\U00002600-\U000027BF"
    r"\U0000FE00-\U0000FE0F"   # variation selectors
    r"\u200d"                   # ZWJ
    r"\U0001F1E0-\U0001F1FF"   # flags
    r"]+$",
    re.UNICODE,
)


def _looks_like_emoji(token: str) -> bool:
    return bool(_CUSTOM_EMOJI_RE.match(token)) or bool(_UNICODE_EMOJI_RE.match(token))


async def _resolve_role(token: str, guild: discord.Guild) -> discord.Role | None:
    """Try to resolve a token as a role (mention, ID, or name)."""
    # <@&id>
    m = _ROLE_MENTION_RE.match(token)
    if m:
        return guild.get_role(int(m.group(1)))
    # bare ID
    if _SNOWFLAKE_RE.match(token):
        return guild.get_role(int(token))
    # name (case-insensitive fallback)
    return discord.utils.find(lambda r: r.name.lower() == token.lower(), guild.roles)


async def _parse_pairs(
    tokens: list[str],
    guild: discord.Guild,
    invoker: discord.Member | None = None,
    bot_owner_ids: list[int] | None = None,
) -> tuple[list[tuple[str, discord.Role]], str | None]:
    """
    Parse a flat list of alternating [emoji, role] tokens.
    Returns (pairs_list, error_message). If error_message is set, pairs_list is empty.
    invoker is checked for role hierarchy — cannot configure roles at/above their top role.
    Server owner and bot owners (from config) are exempt from the hierarchy check.
    """
    if not tokens:
        return [], "No emoji+role pairs provided."

    if len(tokens) % 2 != 0:
        return [], (
            f"Expected **even** number of arguments (emoji + role pairs), "
            f"got {len(tokens)}. Example: `🎮 @Gaming 📣 @Updates`"
        )

    if len(tokens) // 2 > MAX_PAIRS:
        return [], f"Too many pairs! Maximum is **{MAX_PAIRS}** emoji+role pairs."

    pairs: list[tuple[str, discord.Role]] = []
    seen_roles: set[int] = set()
    seen_emojis: set[str] = set()

    for i in range(0, len(tokens), 2):
        emoji_tok = tokens[i]
        role_tok  = tokens[i + 1]

        # --- validate emoji ---
        if not _looks_like_emoji(emoji_tok):
            return [], (
                f"Argument #{i + 1} — expected an emoji but got `{emoji_tok}`.\n"
                f"Make sure you alternate **emoji then role**: `emoji @role emoji @role …`"
            )
        if emoji_tok in seen_emojis:
            return [], f"Duplicate emoji **{emoji_tok}**. Each emoji can only appear once."
        seen_emojis.add(emoji_tok)

        # --- validate role ---
        role = await _resolve_role(role_tok, guild)
        if role is None:
            return [], (
                f"Argument #{i + 2} — could not find a role matching `{role_tok}`.\n"
                f"Use a @mention, a role ID, or the exact role name."
            )
        if role.id in seen_roles:
            return [], f"Duplicate role **{role.name}**. Each role can only appear once."
        if role == guild.default_role:
            return [], "Cannot use @everyone as a button role."
        if role.managed:
            return [], f"**{role.name}** is a managed role (bot/integration role) and cannot be assigned."
        # Hierarchy check — invoker cannot configure roles at or above their own top role
        # Server owner and bot owners (from config) are fully exempt
        if invoker is not None and invoker.id != guild.owner_id and invoker.id not in (bot_owner_ids or []):
            if role >= invoker.top_role:
                return [], (
                    f"**{role.name}** is at or above your highest role. "
                    f"You can only create buttons for roles below your own."
                )
        seen_roles.add(role.id)

        pairs.append((emoji_tok, role))

    return pairs, None


# ── Persistent View & Button ───────────────────────────────────────────────────

class RoleButton(discord.ui.Button):
    """A single persistent button that toggles one role for the clicking member."""

    def __init__(self, emoji: str, role_id: int):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji=emoji,
            custom_id=f"br_role:{role_id}",  # unique per role; encodes target role
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        # Defer immediately so we never miss the 3-second response window and
        # so we never accidentally call response.send_message twice.
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except discord.InteractionResponded:
            pass

        if not interaction.guild:
            return await interaction.followup.send(
                "❌ This only works inside a server.", ephemeral=True
            )

        role = interaction.guild.get_role(self.role_id)
        if role is None:
            return await interaction.followup.send(
                "❌ That role no longer exists on this server. Ask a moderator to update the button roles.",
                ephemeral=True,
            )

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            return await interaction.followup.send(
                "❌ Could not find your member profile. Please try again.", ephemeral=True
            )

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Button Roles self-service")
                await interaction.followup.send(
                    f"✅ Removed **{role.name}** from you.", ephemeral=True
                )
            else:
                await member.add_roles(role, reason="Button Roles self-service")
                await interaction.followup.send(
                    f"✅ Added **{role.name}** to you.", ephemeral=True
                )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to manage that role. "
                "Make sure my role is above it in the role list.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Something went wrong: {e}", ephemeral=True)


class ButtonRolesView(discord.ui.View):
    """
    Persistent view (timeout=None) holding up to MAX_PAIRS RoleButtons.
    custom_ids must be globally stable so discord.py can route interactions
    after a bot restart.
    """

    def __init__(self, pairs: list[tuple[str, int]]):
        super().__init__(timeout=None)
        for emoji, role_id in pairs:
            self.add_item(RoleButton(emoji=emoji, role_id=role_id))


# ── Embed builder ──────────────────────────────────────────────────────────────

def _build_info_embed(pairs: list[tuple[str, discord.Role | int]]) -> discord.Embed:
    """Build the descriptive embed sent above the buttons."""
    embed = discord.Embed(
        title="🎭  Role Selection",
        description="Click a button below to **get** or **remove** a role!",
        colour=COL_INFO,
    )
    lines = []
    for emoji, role in pairs:
        if isinstance(role, discord.Role):
            lines.append(f"{emoji}  →  {role.mention}")
        else:
            lines.append(f"{emoji}  →  <@&{role}>")
    embed.add_field(name="Button Guide", value="\n".join(lines), inline=False)
    embed.set_footer(text="Clicking a button is private — only you can see the response.")
    return embed


# ── Cog ────────────────────────────────────────────────────────────────────────

class ButtonRoles(commands.Cog):
    def __init__(self, bot: DiscordBot):
        self.bot = bot

    async def cog_load(self):
        """
        Re-register all persistent button views from saved data so buttons
        keep working after a bot restart without needing to be re-sent.
        """
        data = _load()
        all_pairs: list[tuple[str, int]] = []
        seen: set[int] = set()

        for guild_data in data.values():
            for msg_data in guild_data.values():
                for emoji, role_id in msg_data.get("pairs", []):
                    if role_id not in seen:
                        all_pairs.append((emoji, role_id))
                        seen.add(role_id)

        if all_pairs:
            # One catch-all view covers all stored role buttons across all messages.
            # discord.py routes interactions by custom_id, so this is sufficient.
            self.bot.add_view(ButtonRolesView(all_pairs))

    # ── Shared send logic ───────────────────────────────────────────────

    async def _send_button_roles(
        self,
        channel: discord.TextChannel,
        pairs: list[tuple[str, discord.Role]],
    ) -> discord.Message:
        """Build and send the embed+buttons, then persist the data."""
        role_pairs: list[tuple[str, int]] = [(emoji, role.id) for emoji, role in pairs]

        embed = _build_info_embed(pairs)                       # uses Role objects for mentions
        view  = ButtonRolesView(role_pairs)
        msg   = await channel.send(embed=embed, view=view)

        # Persist so we can re-register on restart
        data = _load()
        gkey = str(channel.guild.id)
        if gkey not in data:
            data[gkey] = {}
        data[gkey][str(msg.id)] = {
            "channel_id": channel.id,
            "pairs": role_pairs,   # [(emoji_str, role_id), ...]
        }
        await asyncio.to_thread(_save, data)
        return msg

    # ── Prefix command ──────────────────────────────────────────────────

    @commands.command(name="br", aliases=["buttonrole", "buttonroles"])
    @commands.guild_only()
    @permissions.has_permissions(manage_roles=True)
    async def br(self, ctx: CustomContext, channel: discord.TextChannel, *args):
        """
        Send a button-based role picker to a channel (up to 15 emoji+role pairs).
        Usage: !br #channel emoji1 @role1 emoji2 @role2 ...
        Example: !br #roles 🎮 @Gaming 📣 @Updates 🎨 @Art
        Roles are toggled — clicking a button gives the role, clicking again removes it.
        """
        if not args:
            return await ctx.send(embed=discord.Embed(
                title="ℹ️  Button Roles Usage",
                description=(
                    "**Usage:** `!br #channel emoji1 @role1 emoji2 @role2 …`\n"
                    "**Example:** `!br #roles 🎮 @Gaming 📣 @Updates 🎨 @Art`\n\n"
                    f"Supports up to **{MAX_PAIRS}** emoji+role pairs.\n"
                    "Each button toggles the role — click to get it, click again to remove."
                ),
                colour=COL_INFO,
            ))

        pairs, error = await _parse_pairs(list(args), ctx.guild, invoker=ctx.author, bot_owner_ids=self.bot.config.discord_owner_ids)
        if error:
            return await ctx.send(embed=discord.Embed(
                description=f"❌  {error}", colour=COL_ERROR
            ))

        try:
            msg = await self._send_button_roles(channel, pairs)
        except discord.Forbidden:
            return await ctx.send(embed=discord.Embed(
                description=f"❌  I don't have permission to send messages in {channel.mention}.",
                colour=COL_ERROR,
            ))
        except Exception as e:
            return await ctx.send(embed=discord.Embed(
                description=f"❌  Unexpected error: {e}", colour=COL_ERROR
            ))

        await ctx.send(embed=discord.Embed(
            title="✅  Button Roles Created",
            description=(
                f"Sent **{len(pairs)}** role button(s) to {channel.mention}.\n"
                f"[Jump to message]({msg.jump_url})"
            ),
            colour=COL_SUCCESS,
        ))

    # ── Slash command ───────────────────────────────────────────────────

    @app_commands.command(
        name="br",
        description="Send a button-based role picker to a channel (up to 15 emoji+role pairs).",
    )
    @app_commands.describe(
        channel="The channel to send the role buttons to.",
        pairs=(
            "Alternating emoji and role IDs/mentions. "
            'Example: "🎮 @Gaming 📣 @Updates 🎨 @Art" (up to 15 pairs)'
        ),
    )
    @app_commands.guild_only()
    @permissions.slash_has_permissions(manage_roles=True)
    async def slash_br(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        pairs: str,
    ):
        await interaction.response.defer(ephemeral=True)

        # Tokenise — whitespace split works for both unicode emoji and <:name:id> forms
        tokens = pairs.split()

        invoker = interaction.guild.get_member(interaction.user.id)
        parsed, error = await _parse_pairs(tokens, interaction.guild, invoker=invoker, bot_owner_ids=self.bot.config.discord_owner_ids)
        if error:
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="ℹ️  Button Roles — Input Error",
                    description=(
                        f"❌  {error}\n\n"
                        "**Format:** `emoji role_id_or_mention emoji role_id_or_mention …`\n"
                        "**Example:** `🎮 123456789012345678 🎵 987654321098765432`\n"
                        "You can also use role @mentions if the slash command expands them."
                    ),
                    colour=COL_ERROR,
                ),
                ephemeral=True,
            )

        try:
            msg = await self._send_button_roles(channel, parsed)
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=discord.Embed(
                    description=f"❌  I don't have permission to send messages in {channel.mention}.",
                    colour=COL_ERROR,
                ),
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                embed=discord.Embed(description=f"❌  Unexpected error: {e}", colour=COL_ERROR),
                ephemeral=True,
            )

        await interaction.followup.send(
            embed=discord.Embed(
                title="✅  Button Roles Created",
                description=(
                    f"Sent **{len(parsed)}** role button(s) to {channel.mention}.\n"
                    f"[Jump to message]({msg.jump_url})"
                ),
                colour=COL_SUCCESS,
            ),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ButtonRoles(bot))
