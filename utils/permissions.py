import discord
import os
from typing import Union, TYPE_CHECKING
from discord.ext import commands
from discord import app_commands

if TYPE_CHECKING:
    from utils.default import CustomContext

# ── Bot owners — loaded from .env ─────────────────────────────────────────────
# Users in this list AND the guild's server owner bypass ALL permission checks
# and ALL Paladin protection filters.
_owner_ids_raw = os.environ.get("DISCORD_OWNER_IDS", "828960010359930880,446345650309955614")
OWNERS: list[int] = [int(x.strip()) for x in _owner_ids_raw.split(",") if x.strip()]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_privileged_ctx(ctx: "CustomContext") -> bool:
    """True if the prefix-command invoker has universal bypass."""
    if ctx.author.id in OWNERS:
        return True
    if ctx.guild and ctx.author.id == ctx.guild.owner_id:
        return True
    return False


def _is_privileged_interaction(interaction: discord.Interaction) -> bool:
    """True if the slash-command invoker has universal bypass."""
    if interaction.user.id in OWNERS:
        return True
    if interaction.guild and interaction.user.id == interaction.guild.owner_id:
        return True
    return False


def is_owner(ctx: "CustomContext") -> bool:
    return _is_privileged_ctx(ctx)


# ── Prefix permission check ───────────────────────────────────────────────────

async def check_permissions(ctx: "CustomContext", perms, *, check=all) -> bool:
    """Owners / server owner bypass unconditionally; others need the guild perm."""
    if _is_privileged_ctx(ctx):
        return True
    guild_perms = ctx.author.guild_permissions
    return check(getattr(guild_perms, name, False) == value for name, value in perms.items())


def has_permissions(*, check=all, **perms) -> commands.check:
    async def pred(ctx: "CustomContext"):
        result = await check_permissions(ctx, perms, check=check)
        if not result:
            perm_names = ", ".join(f"`{p.replace('_', ' ').title()}`" for p in perms)
            await ctx.send(embed=discord.Embed(
                description=f"❌ You need the {perm_names} permission{'s' if len(perms) > 1 else ''} to use this.",
                colour=discord.Colour.red()
            ))
        return result
    return commands.check(pred)


# ── Slash permission check ────────────────────────────────────────────────────

def slash_has_permissions(**perms):
    """Owners / server owner bypass unconditionally; others need the guild perm."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if _is_privileged_interaction(interaction):
            return True
        if not interaction.guild:
            raise app_commands.CheckFailure("❌ This command can only be used in a server.")
        guild_perms = interaction.user.guild_permissions
        missing = [p for p, v in perms.items() if not getattr(guild_perms, p, False)]
        if missing:
            names = ", ".join(f"`{p.replace('_', ' ').title()}`" for p in missing)
            raise app_commands.CheckFailure(
                f"❌ You need the {names} permission{'s' if len(missing) > 1 else ''} to use this."
            )
        return True
    return app_commands.check(predicate)


# ── Privilege escalation guard (check_priv) ───────────────────────────────────

async def check_priv(ctx: "CustomContext", member: discord.Member) -> bool:
    """
    Returns True if the action should be BLOCKED (error already sent).
    Returns False if the action is allowed.
    Owners and server owners bypass all role-hierarchy checks.
    """
    if member.id == ctx.author.id:
        await ctx.send(embed=discord.Embed(description=f"❌ You can't {ctx.command.name} yourself.", colour=discord.Colour.red()))
        return True
    if member.id == ctx.bot.user.id:
        await ctx.send(embed=discord.Embed(description="❌ Nice try, but I won't do that to myself.", colour=discord.Colour.red()))
        return True

    # Universal bypass
    if _is_privileged_ctx(ctx):
        return False

    # Protect owners from non-owners
    if member.id in OWNERS:
        await ctx.send(embed=discord.Embed(description=f"❌ I can't {ctx.command.name} my owner.", colour=discord.Colour.red()))
        return True
    if member.id == ctx.guild.owner_id:
        await ctx.send(embed=discord.Embed(description=f"❌ You can't {ctx.command.name} the server owner.", colour=discord.Colour.red()))
        return True
    if ctx.author.top_role <= member.top_role:
        await ctx.send(embed=discord.Embed(description=f"❌ You can't {ctx.command.name} someone with an equal or higher role than you.", colour=discord.Colour.red()))
        return True
    return False


async def slash_check_priv(interaction: discord.Interaction, member: discord.Member) -> bool:
    """Returns True if the action should be BLOCKED (error already sent)."""
    async def send_err(msg):
        embed = discord.Embed(description=msg, colour=discord.Colour.red())
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

    if member.id == interaction.user.id:
        await send_err("❌ You can't do that to yourself.")
        return True
    if member.id == interaction.client.user.id:
        await send_err("❌ Nice try, but I won't do that to myself.")
        return True

    # Universal bypass
    if _is_privileged_interaction(interaction):
        return False

    # Protect owners from non-owners
    if member.id in OWNERS:
        await send_err("❌ I can't do that to my owner.")
        return True
    if member.id == interaction.guild.owner_id:
        await send_err("❌ You can't do that to the server owner.")
        return True
    if interaction.user.top_role <= member.top_role:
        await send_err("❌ You can't do that to someone with an equal or higher role than you.")
        return True
    return False


# ── Misc ──────────────────────────────────────────────────────────────────────

def can_handle(ctx: "CustomContext", permission: str) -> bool:
    return (
        isinstance(ctx.channel, discord.DMChannel) or
        getattr(ctx.channel.permissions_for(ctx.guild.me), permission)
    )
