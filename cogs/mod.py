import discord
import re
import asyncio
import datetime

from discord.ext import commands
from discord import app_commands
from utils.default import CustomContext
from utils.data import DiscordBot
from utils import permissions, default
from utils.permissions import OWNERS

COL_SUCCESS = discord.Colour.green()
COL_ERROR   = discord.Colour.red()
COL_WARN    = discord.Colour.orange()
COL_INFO    = discord.Colour.blurple()
COL_MOD     = discord.Colour.from_str("#E74C3C")
COL_CONFIRM = discord.Colour.from_str("#F39C12")


def err(text):
    return discord.Embed(description=f"❌  {text}", colour=COL_ERROR)

def ok(text):
    return discord.Embed(description=f"✅  {text}", colour=COL_SUCCESS)


# ── Custom permission decorator ───────────────────────────────────────────────
#
# Used on every hybrid command.  Logic:
#   1. OWNERS list          → always True
#   2. ctx.guild.owner_id   → always True
#   3. Otherwise            → check the specific Discord guild permission
#
# For slash invocations discord.py routes through the same predicate because
# hybrid commands share their check stack.

def mod_check(**required_perms):
    """
    Decorator for hybrid mod commands.
    Owners / server owner bypass unconditionally; others need the listed guild perm(s).
    Sends an error embed on failure.
    """
    async def predicate(ctx: commands.Context) -> bool:
        # Universal bypass
        if ctx.author.id in OWNERS:
            return True
        if ctx.guild and ctx.author.id == ctx.guild.owner_id:
            return True

        # Regular guild permission check
        guild_perms = ctx.author.guild_permissions
        missing = [p for p in required_perms if not getattr(guild_perms, p, False)]
        if missing:
            names = ", ".join(f"`{p.replace('_', ' ').title()}`" for p in missing)
            embed = discord.Embed(
                description=f"❌ You need the {names} permission{'s' if len(missing) > 1 else ''} to use this.",
                colour=COL_ERROR,
            )
            # FIX #4: Properly handle slash vs prefix commands to avoid double messages
            try:
                if ctx.interaction:
                    # Slash command: use interaction response
                    await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    # Prefix command: use standard ctx.send
                    await ctx.send(embed=embed, ephemeral=True)
            except Exception:
                pass
            return False
        return True

    return commands.check(predicate)


# ── Paladin helper ─────────────────────────────────────────────────────────────

async def paladin_check(bot: DiscordBot, guild: discord.Guild, actor, action: str):
    """
    Register a mod action attempt with Paladin.
    Skips automatically if the actor is an OWNER, server owner, or whitelisted.
    """
    paladin_cog = bot.cogs.get("Paladin")
    if not paladin_cog:
        return
    # Paladin._register_attempt handles all bypass logic internally
    member = guild.get_member(actor.id) if isinstance(actor, discord.User) else actor
    if member:
        await paladin_cog._register_attempt(guild, member, action)


# ── Runtime permission re-check ────────────────────────────────────────────────

def _still_has_perm(member: discord.Member, perm: str) -> bool:
    """Re-check a guild permission at execution time (post-Paladin strip)."""
    if member.id in OWNERS:
        return True
    if member.id == member.guild.owner_id:
        return True
    return getattr(member.guild_permissions, perm, False)


# ── Confirmation Views ─────────────────────────────────────────────────────────

class ConfirmView(discord.ui.View):
    def __init__(self, invoker_id: int, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.invoker_id  = invoker_id
        self.confirmed   = None
        self.interaction = None

    async def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "❌ Only the command invoker can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction): return
        self.confirmed   = True
        self.interaction = interaction
        self._disable_all()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction): return
        self.confirmed   = False
        self.interaction = interaction
        self._disable_all()
        self.stop()

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def on_timeout(self):
        self._disable_all()


# ── Confirm helpers ────────────────────────────────────────────────────────────

async def send_confirm(ctx: CustomContext, embed: discord.Embed):
    view = ConfirmView(ctx.author.id)
    msg  = await ctx.send(embed=embed, view=view)
    await view.wait()
    try:
        await msg.edit(view=view)
    except discord.NotFound:
        pass
    if view.confirmed is None:
        await msg.edit(embed=discord.Embed(description="⏳  Action cancelled — timed out.", colour=COL_WARN), view=view)
        return False, msg, None
    return view.confirmed, msg, view.interaction


async def slash_confirm(interaction: discord.Interaction, embed: discord.Embed):
    view = ConfirmView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await view.wait()
    try:
        await interaction.edit_original_response(view=view)
    except Exception:
        pass
    if view.confirmed is None:
        try:
            await interaction.edit_original_response(
                embed=discord.Embed(description="⏳  Action cancelled — timed out.", colour=COL_WARN),
                view=view,
            )
        except Exception:
            pass
        return False, None
    return view.confirmed, view.interaction


# ── Converters ─────────────────────────────────────────────────────────────────

class MemberID(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            m = await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                return int(argument, base=10)
            except ValueError:
                raise commands.BadArgument(f"`{argument}` is not a valid member or member ID.") from None
        return m.id


class ActionReason(commands.Converter):
    async def convert(self, ctx, argument):
        if len(argument) > 512:
            raise commands.BadArgument(f"Reason too long ({len(argument)}/512 chars).")
        return argument


def parse_ids(raw: str) -> list[int]:
    ids = []
    for part in raw.replace(",", " ").split():
        try:
            ids.append(int(part.strip("<@!> ")))
        except ValueError:
            pass
    return ids


def parse_duration(raw: str) -> datetime.timedelta | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        td = datetime.timedelta(minutes=int(raw))
    else:
        pattern = r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?"
        m = re.fullmatch(pattern, raw)
        if not m or not any(m.groups()):
            return None
        td = datetime.timedelta(
            days=int(m.group(1) or 0),
            hours=int(m.group(2) or 0),
            minutes=int(m.group(3) or 0),
            seconds=int(m.group(4) or 0),
        )
    if td.total_seconds() <= 0 or td > datetime.timedelta(days=28):
        return None
    return td


# ── Cog ────────────────────────────────────────────────────────────────────────

class Moderator(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot

    # ── Kick ──────────────────────────────────────────────────────────

    def _kick_embed(self, member, moderator, reason):
        embed = discord.Embed(title="👢  Member Kicked", colour=COL_MOD)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Member",    value=f"{member.mention}\n`{member}`", inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,              inline=True)
        embed.add_field(name="📝 Reason",    value=reason or "No reason provided",  inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        return embed

    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @commands.guild_only()
    @app_commands.describe(member="Member to kick", reason="Reason for kick")
    @mod_check(kick_members=True)
    async def kick(self, ctx: CustomContext, member: discord.Member, *, reason: str = None):
        """Kick a member from the server."""
        if await permissions.check_priv(ctx, member): return

        # Paladin strike is registered by the audit-log listener in paladin.py
        # after the action actually executes — do NOT call paladin_check here.

        embed = discord.Embed(
            title="⚠️  Confirm Kick",
            description=f"Are you sure you want to kick {member.mention}?\n**Reason:** {reason or 'No reason provided'}",
            colour=COL_CONFIRM,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Expires in 30 seconds.")

        if ctx.interaction:
            confirmed, btn = await slash_confirm(ctx.interaction, embed)
            if not confirmed: return
            if not _still_has_perm(ctx.author, "kick_members"):
                return await btn.response.edit_message(
                    embed=err("❌ You no longer have permission to kick members."), view=None)
            try:
                await member.kick(reason=default.responsible(ctx.author, reason))
                await btn.response.edit_message(embed=self._kick_embed(member, ctx.author, reason), view=None)
            except Exception as e:
                await btn.response.edit_message(embed=err(e), view=None)
        else:
            confirmed, msg, interaction = await send_confirm(ctx, embed)
            if not confirmed:
                if interaction:
                    await interaction.response.edit_message(
                        embed=discord.Embed(description="❌  Kick cancelled.", colour=COL_WARN), view=None)
                return
            if not _still_has_perm(ctx.author, "kick_members"):
                return await interaction.response.edit_message(
                    embed=err("❌ You no longer have permission to kick members."), view=None)
            try:
                await member.kick(reason=default.responsible(ctx.author, reason))
                await interaction.response.edit_message(embed=self._kick_embed(member, ctx.author, reason), view=None)
            except Exception as e:
                await interaction.response.edit_message(embed=err(e), view=None)

    # ── Ban ────────────────────────────────────────────────────────────

    def _ban_embed(self, user, moderator, reason):
        embed = discord.Embed(title="🔨  Member Banned", colour=COL_MOD)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="👤 User",      value=f"{user.mention}\n`{user}`", inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,          inline=True)
        embed.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
        embed.set_footer(text=f"User ID: {user.id}")
        return embed

    @commands.hybrid_command(name="ban", description="Ban a member from the server.")
    @commands.guild_only()
    @app_commands.describe(member="Member to ban", reason="Reason for ban")
    @mod_check(ban_members=True)
    async def ban(self, ctx: CustomContext, member: discord.Member, *, reason: str = None):
        """Ban a member from the server."""
        if await permissions.check_priv(ctx, member): return

        # Paladin strike registered by on_member_ban audit-log listener after execution.

        embed = discord.Embed(
            title="⚠️  Confirm Ban",
            description=f"Are you sure you want to ban {member.mention}?\n**Reason:** {reason or 'No reason provided'}",
            colour=COL_CONFIRM,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Expires in 30 seconds.")

        if ctx.interaction:
            confirmed, btn = await slash_confirm(ctx.interaction, embed)
            if not confirmed: return
            if not _still_has_perm(ctx.author, "ban_members"):
                return await btn.response.edit_message(
                    embed=err("❌ You no longer have permission to ban members."), view=None)
            try:
                await member.ban(reason=default.responsible(ctx.author, reason))
                await btn.response.edit_message(embed=self._ban_embed(member, ctx.author, reason), view=None)
            except Exception as e:
                await btn.response.edit_message(embed=err(e), view=None)
        else:
            confirmed, msg, interaction = await send_confirm(ctx, embed)
            if not confirmed:
                if interaction:
                    await interaction.response.edit_message(
                        embed=discord.Embed(description="❌  Ban cancelled.", colour=COL_WARN), view=None)
                return
            if not _still_has_perm(ctx.author, "ban_members"):
                return await interaction.response.edit_message(
                    embed=err("❌ You no longer have permission to ban members."), view=None)
            try:
                await member.ban(reason=default.responsible(ctx.author, reason))
                await interaction.response.edit_message(embed=self._ban_embed(member, ctx.author, reason), view=None)
            except Exception as e:
                await interaction.response.edit_message(embed=err(e), view=None)

    # ── Unban ──────────────────────────────────────────────────────────

    @commands.hybrid_command(name="unban", description="Unban a user by their ID.")
    @commands.guild_only()
    @app_commands.describe(user_id="The user ID to unban", reason="Reason for unban")
    @mod_check(ban_members=True)
    async def unban(self, ctx: CustomContext, user_id: str, *, reason: str = None):
        """Unban a user from the server."""
        try:
            uid    = int(user_id)
            target = await self.bot.fetch_user(uid)
            await ctx.guild.unban(discord.Object(id=uid), reason=default.responsible(ctx.author, reason))
            embed = discord.Embed(title="✅  Member Unbanned", colour=COL_SUCCESS)
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="👤 User",      value=f"`{target}`",      inline=True)
            embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
            embed.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
            embed.set_footer(text=f"User ID: {uid}")
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Mute ───────────────────────────────────────────────────────────

    async def _get_or_create_muted_role(self, guild: discord.Guild) -> discord.Role:
        role = discord.utils.get(guild.roles, name="Muted")
        
        if role is None:
            role = await guild.create_role(
                name="Muted",
                colour=discord.Colour.dark_grey(),
                reason="Auto-created by mute command",
            )
            
        denies = [
            "send_messages",
            "add_reactions",
            "create_public_threads",
            "create_private_threads",
            "send_messages_in_threads",
            "speak"
        ]

        # Scan ALL server channels to enforce Muted role overwrites
        for channel in guild.channels:
            overwrite = channel.overwrites_for(role)
            needs_update = False

            # Check if any permission is missing the False (deny) state
            for perm in denies:
                if getattr(overwrite, perm) is not False:
                    setattr(overwrite, perm, False)
                    needs_update = True

            # Only trigger an API call if an overwrite edit was actually needed
            if needs_update:
                try:
                    await channel.set_permissions(
                        role, 
                        overwrite=overwrite, 
                        reason="Auto-configuring Muted role restrictions"
                    )
                except Exception:
                    # Ignore Forbidden / HTTPException for channels bot lacks access to modify
                    pass

        return role

    def _mute_embed(self, member, moderator, reason, title, colour):
        embed = discord.Embed(title=title, colour=colour)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Member",    value=f"{member.mention}\n`{member}`", inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,              inline=True)
        embed.add_field(name="📝 Reason",    value=reason or "No reason provided",  inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        return embed

    @commands.hybrid_command(name="mute", description="Mute a member (role-based).")
    @commands.guild_only()
    @app_commands.describe(member="Member to mute", reason="Reason for mute")
    @mod_check(manage_roles=True)
    async def mute(self, ctx: CustomContext, member: discord.Member, *, reason: str = None):
        """Mute a member. Auto-creates a Muted role with channel overwrites if needed."""
        if await permissions.check_priv(ctx, member): return

        try:
            muted_role = await self._get_or_create_muted_role(ctx.guild)
        except discord.Forbidden:
            return await ctx.send(embed=err("I don't have permission to create roles or manage channel overwrites."), ephemeral=True)
        except Exception as e:
            return await ctx.send(embed=err(f"Failed to set up Muted role: {e}"), ephemeral=True)

        try:
            await member.add_roles(muted_role, reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._mute_embed(member, ctx.author, reason, "🔇  Member Muted", COL_WARN))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    @commands.hybrid_command(name="unmute", description="Unmute a member.")
    @commands.guild_only()
    @app_commands.describe(member="Member to unmute", reason="Reason for unmute")
    @mod_check(manage_roles=True)
    async def unmute(self, ctx: CustomContext, member: discord.Member, *, reason: str = None):
        """Unmute a member."""
        if await permissions.check_priv(ctx, member): return
        muted_role = next((r for r in ctx.guild.roles if r.name == "Muted"), None)
        if not muted_role:
            return await ctx.send(embed=err("No **Muted** role found on this server."), ephemeral=True)
        try:
            await member.remove_roles(muted_role, reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._mute_embed(member, ctx.author, reason, "🔊  Member Unmuted", COL_SUCCESS))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Timeout ────────────────────────────────────────────────────────

    def _timeout_embed(self, member, moderator, duration_str, reason, until: datetime.datetime):
        embed = discord.Embed(title="⏱️  Member Timed Out", colour=COL_WARN)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Member",    value=f"{member.mention}\n`{member}`",     inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,                  inline=True)
        embed.add_field(name="⏳ Duration",  value=f"`{duration_str}`",                inline=True)
        embed.add_field(name="🕐 Expires",   value=discord.utils.format_dt(until, "R"), inline=True)
        embed.add_field(name="📝 Reason",    value=reason or "No reason provided",      inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        return embed

    def _untimeout_embed(self, member, moderator, reason):
        embed = discord.Embed(title="✅  Timeout Removed", colour=COL_SUCCESS)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Member",    value=f"{member.mention}\n`{member}`", inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,              inline=True)
        embed.add_field(name="📝 Reason",    value=reason or "No reason provided",  inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        return embed

    @commands.hybrid_command(name="timeout", aliases=["to"], description="Timeout a member for a given duration.")
    @commands.guild_only()
    @app_commands.describe(
        member="Member to timeout",
        duration="Duration: 10, 30m, 2h, 1d, 1h30m (bare number = minutes, max 28d)",
        reason="Reason for timeout",
    )
    @mod_check(moderate_members=True)
    async def timeout(self, ctx: CustomContext, member: discord.Member, duration: str, *, reason: str = None):
        """Timeout a member for a given duration."""
        if await permissions.check_priv(ctx, member): return

        td = parse_duration(duration)
        if td is None:
            return await ctx.send(embed=err(
                "Invalid duration. Examples: `10` (10 min), `30m`, `2h`, `1d`, `1h30m`. Max is 28 days."
            ), ephemeral=True)

        until = datetime.datetime.now(datetime.timezone.utc) + td

        try:
            await member.timeout(until, reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._timeout_embed(member, ctx.author, duration, reason, until))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    @commands.hybrid_command(name="untimeout", aliases=["uto"], description="Remove a timeout from a member.")
    @commands.guild_only()
    @app_commands.describe(member="Member to un-timeout", reason="Reason")
    @mod_check(moderate_members=True)
    async def untimeout(self, ctx: CustomContext, member: discord.Member, *, reason: str = None):
        """Remove a timeout from a member."""
        if await permissions.check_priv(ctx, member): return
        if not member.timed_out_until:
            return await ctx.send(embed=err(f"{member.mention} is not currently timed out."), ephemeral=True)
        try:
            await member.timeout(None, reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._untimeout_embed(member, ctx.author, reason))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Nickname ───────────────────────────────────────────────────────

    @commands.hybrid_command(name="nickname", aliases=["nick"], description="Change a member's nickname.")
    @commands.guild_only()
    @app_commands.describe(member="Member to rename (leave blank for bot)", name="New nickname (leave blank to clear)")
    @mod_check(manage_nicknames=True)
    async def nickname(self, ctx: CustomContext, member: discord.Member = None, *, name: str = None):
        """Change a nickname. No member = bot's nickname."""
        if member is None:
            try:
                await ctx.guild.me.edit(nick=name)
                return await ctx.send(embed=discord.Embed(title="✏️  Bot Nickname Updated",
                    description=f"Set to **{name}**." if name else "Nickname cleared.", colour=COL_SUCCESS))
            except Exception as e:
                return await ctx.send(embed=err(e), ephemeral=True)
        if await permissions.check_priv(ctx, member): return
        try:
            old = member.nick or member.name
            await member.edit(nick=name, reason=default.responsible(ctx.author, "Nickname changed"))
            embed = discord.Embed(title="✏️  Nickname Updated", colour=COL_SUCCESS)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="👤 Member", value=member.mention, inline=True)
            embed.add_field(name="Before",    value=f"`{old}`",     inline=True)
            embed.add_field(name="After",     value=f"`{name}`" if name else "*Cleared*", inline=True)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Role ───────────────────────────────────────────────────────────

    def _role_embed(self, member, role, moderator, action: str):
        added = action == "add"
        embed = discord.Embed(
            title=f"{'✅' if added else '➖'}  Role {'Added' if added else 'Removed'}",
            colour=COL_SUCCESS if added else COL_WARN,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Member",    value=f"{member.mention}\n`{member}`", inline=True)
        embed.add_field(name="🎭 Role",      value=role.mention,                    inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,              inline=True)
        embed.set_footer(text=f"User ID: {member.id}")
        return embed

    def _role_hierarchy_check(self, invoker: discord.Member, role: discord.Role) -> str | None:
        if role == invoker.guild.default_role:
            return "Cannot assign @everyone."
        if role.managed:
            return f"**{role.name}** is a managed (bot/integration) role and cannot be assigned manually."
        # Owners / server owner skip hierarchy check
        if invoker.id not in OWNERS and invoker.id != invoker.guild.owner_id:
            if role >= invoker.top_role:
                return f"**{role.name}** is at or above your highest role. You can only assign roles below your own."
        if role >= invoker.guild.me.top_role:
            return f"**{role.name}** is at or above my highest role. Please move my role above it."
        return None

    @commands.hybrid_command(name="role", description="Add or remove a role from a member (toggles).")
    @commands.guild_only()
    @app_commands.describe(member="The member to give or remove the role from", role="The role to toggle")
    @mod_check(manage_roles=True)
    async def role(self, ctx: CustomContext, member: discord.Member, *, role: discord.Role):
        """
        Toggle a role on a member. Removes it if they have it, adds it otherwise.
        You can only assign roles equal to or below your own top role.
        """
        error = self._role_hierarchy_check(ctx.author, role)
        if error:
            return await ctx.send(embed=err(error), ephemeral=True)

        try:
            if role in member.roles:
                await member.remove_roles(role, reason=default.responsible(ctx.author, "Role command"))
                await ctx.send(embed=self._role_embed(member, role, ctx.author, "remove"))
            else:
                await member.add_roles(role, reason=default.responsible(ctx.author, "Role command"))
                await ctx.send(embed=self._role_embed(member, role, ctx.author, "add"))
        except discord.Forbidden:
            await ctx.send(embed=err("I don't have permission to manage that role. Make sure my role is above it."), ephemeral=True)
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Purge ──────────────────────────────────────────────────────────

    @commands.hybrid_command(name="purge", description="Delete a number of messages from this channel.")
    @commands.guild_only()
    @commands.max_concurrency(1, per=commands.BucketType.guild)
    @app_commands.describe(amount="Number of messages to delete (max 100)")
    @mod_check(manage_messages=True)
    async def purge(self, ctx: CustomContext, amount: int = 10):
        """Delete messages from this channel."""
        if amount < 1 or amount > 100:
            return await ctx.send(embed=err("Amount must be between 1 and 100."), ephemeral=True)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.interaction.followup.send(embed=discord.Embed(
                title="🗑️  Messages Purged",
                description=f"Deleted **{len(deleted)}** message{'s' if len(deleted) != 1 else ''}.",
                colour=COL_SUCCESS), ephemeral=True)
        else:
            try:
                await ctx.message.delete()
            except Exception:
                pass
            deleted = await ctx.channel.purge(limit=amount)
            msg = await ctx.send(embed=discord.Embed(
                title="🗑️  Messages Purged",
                description=f"Deleted **{len(deleted)}** message{'s' if len(deleted) != 1 else ''}.",
                colour=COL_SUCCESS))
            await asyncio.sleep(5)
            try:
                await msg.delete()
            except Exception:
                pass

    # ── Slowmode ───────────────────────────────────────────────────────

    def _slowmode_embed(self, seconds, channel, moderator):
        if seconds == 0:
            return discord.Embed(title="🐇  Slowmode Disabled", colour=COL_SUCCESS,
                description=f"Slowmode removed from {channel.mention}.")
        mins, secs  = divmod(seconds, 60)
        hours, mins = divmod(mins, 60)
        parts       = [f"{hours}h" if hours else "", f"{mins}m" if mins else "", f"{secs}s" if secs else ""]
        duration    = " ".join(p for p in parts if p)
        embed = discord.Embed(title="🐢  Slowmode Set", colour=COL_INFO)
        embed.add_field(name="📺 Channel",  value=channel.mention,   inline=True)
        embed.add_field(name="⏱️ Delay",    value=f"`{duration}`",   inline=True)
        embed.add_field(name="🛡️ Set by",   value=moderator.mention, inline=True)
        return embed

    @commands.hybrid_command(name="slowmode", description="Set slowmode for this channel. 0 to disable.")
    @commands.guild_only()
    @app_commands.describe(seconds="Delay in seconds (0 to disable, max 21600)")
    @mod_check(manage_channels=True)
    async def slowmode(self, ctx: CustomContext, seconds: int = 0):
        """Set slowmode for this channel. 0 to disable. Max 21600 (6h)."""
        if seconds < 0 or seconds > 21600:
            return await ctx.send(embed=err("Slowmode must be between **0** and **21600** seconds."), ephemeral=True)
        try:
            await ctx.channel.edit(slowmode_delay=seconds)
            await ctx.send(embed=self._slowmode_embed(seconds, ctx.channel, ctx.author))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Lock / Unlock ──────────────────────────────────────────────────

    def _lock_embed(self, channel, moderator, reason, locked: bool):
        embed = discord.Embed(title="🔒  Channel Locked" if locked else "🔓  Channel Unlocked",
            colour=COL_MOD if locked else COL_SUCCESS)
        embed.add_field(name="📺 Channel",   value=channel.mention,               inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,             inline=True)
        embed.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
        return embed

    @commands.hybrid_command(name="lock", description="Lock a channel so only mods can send messages.")
    @commands.guild_only()
    @app_commands.describe(channel="Channel to lock (default: current)", reason="Reason for lock")
    @mod_check(manage_channels=True)
    async def lock(self, ctx: CustomContext, channel: discord.TextChannel = None, *, reason: str = None):
        """Lock a channel so only mods can send messages."""
        channel   = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        if overwrite.send_messages is False:
            return await ctx.send(embed=err(f"{channel.mention} is already locked."), ephemeral=True)
        overwrite.send_messages = False
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._lock_embed(channel, ctx.author, reason, True))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    @commands.hybrid_command(name="unlock", description="Unlock a previously locked channel.")
    @commands.guild_only()
    @app_commands.describe(channel="Channel to unlock (default: current)", reason="Reason for unlock")
    @mod_check(manage_channels=True)
    async def unlock(self, ctx: CustomContext, channel: discord.TextChannel = None, *, reason: str = None):
        """Unlock a previously locked channel."""
        channel   = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        if overwrite.send_messages is not False:
            return await ctx.send(embed=err(f"{channel.mention} is not locked."), ephemeral=True)
        overwrite.send_messages = None
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._lock_embed(channel, ctx.author, reason, False))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Hide / Unhide ──────────────────────────────────────────────────

    def _hide_embed(self, channel, moderator, reason, hidden: bool):
        embed = discord.Embed(title="👁️  Channel Hidden" if hidden else "👁️  Channel Visible",
            colour=COL_MOD if hidden else COL_SUCCESS)
        embed.add_field(name="📺 Channel",   value=channel.mention,               inline=True)
        embed.add_field(name="🛡️ Moderator", value=moderator.mention,             inline=True)
        embed.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
        return embed

    @commands.hybrid_command(name="hide", description="Hide a channel from regular members.")
    @commands.guild_only()
    @app_commands.describe(channel="Channel to hide (default: current)", reason="Reason")
    @mod_check(manage_channels=True)
    async def hide(self, ctx: CustomContext, channel: discord.TextChannel = None, *, reason: str = None):
        """Hide a channel from regular members."""
        channel   = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        if overwrite.view_channel is False:
            return await ctx.send(embed=err(f"{channel.mention} is already hidden."), ephemeral=True)
        overwrite.view_channel = False
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._hide_embed(channel, ctx.author, reason, True))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    @commands.hybrid_command(name="unhide", description="Make a hidden channel visible again.")
    @commands.guild_only()
    @app_commands.describe(channel="Channel to unhide (default: current)", reason="Reason")
    @mod_check(manage_channels=True)
    async def unhide(self, ctx: CustomContext, channel: discord.TextChannel = None, *, reason: str = None):
        """Make a hidden channel visible again."""
        channel   = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        if overwrite.view_channel is not False:
            return await ctx.send(embed=err(f"{channel.mention} is not hidden."), ephemeral=True)
        overwrite.view_channel = None
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                reason=default.responsible(ctx.author, reason))
            await ctx.send(embed=self._hide_embed(channel, ctx.author, reason, False))
        except Exception as e:
            await ctx.send(embed=err(e), ephemeral=True)

    # ── Announce Role ──────────────────────────────────────────────────

    @commands.hybrid_command(name="announcerole", aliases=["ar"],
        description="Temporarily make a role mentionable for an announcement.")
    @commands.guild_only()
    @app_commands.describe(role="Role to make temporarily mentionable")
    @mod_check(manage_roles=True)
    async def announcerole(self, ctx: CustomContext, *, role: discord.Role):
        """Temporarily make a role mentionable for an announcement."""
        if role == ctx.guild.default_role:
            return await ctx.send(embed=err("Cannot make @everyone/@here mentionable."), ephemeral=True)
        # hierarchy check only for non-owners
        if ctx.author.id not in OWNERS and ctx.author.id != ctx.guild.owner_id:
            if ctx.author.top_role.position <= role.position:
                return await ctx.send(embed=err("That role is above your permission level."), ephemeral=True)
        if ctx.me.top_role.position <= role.position:
            return await ctx.send(embed=err("That role is above my permission level."), ephemeral=True)
        await role.edit(mentionable=True, reason=f"[ {ctx.author} ] announcerole")
        embed = discord.Embed(title="🔔  Role Mentionable",
            description=f"**{role.name}** is now mentionable. Mention it within **30 seconds** or it will revert.",
            colour=COL_INFO)
        msg = await ctx.send(embed=embed)
        for _ in range(10):  # max 10 attempts (5 min total)
            try:
                checker = await self.bot.wait_for("message", timeout=30.0, check=lambda m: role.mention in m.content)
                if checker.author.id == ctx.author.id:
                    await role.edit(mentionable=False, reason=f"[ {ctx.author} ] announcerole")
                    done = discord.Embed(title="✅  Announcement Sent",
                        description=f"**{role.name}** mentioned by {ctx.author.mention} in {checker.channel.mention}.",
                        colour=COL_SUCCESS)
                    return await msg.edit(embed=done)
                else:
                    try: await checker.delete()
                    except discord.HTTPException: pass
            except asyncio.TimeoutError:
                await role.edit(mentionable=False, reason=f"[ {ctx.author} ] announcerole")
                return await msg.edit(embed=discord.Embed(title="⏳  Timed Out",
                    description=f"**{role.name}** was never mentioned. Reverted.", colour=COL_WARN))
        # Exhausted all attempts
        await role.edit(mentionable=False, reason=f"[ {ctx.author} ] announcerole")
        await msg.edit(embed=discord.Embed(title="⏳  Timed Out",
            description=f"**{role.name}** was never mentioned by you. Reverted.", colour=COL_WARN))

    # ── Find ───────────────────────────────────────────────────────────

    @commands.group()
    @commands.guild_only()
    @mod_check(ban_members=True)
    async def find(self, ctx: CustomContext):
        """Find members by various criteria."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(str(ctx.command))

    @find.command(name="playing")
    async def find_playing(self, ctx, *, search: str):
        """Find members playing a specific game."""
        loop = []
        for i in ctx.guild.members:
            if i.activities and not i.bot:
                for g in i.activities:
                    if g.name and search.lower() in g.name.lower():
                        loop.append(f"{i} | {type(g).__name__}: {g.name} ({i.id})")
        await default.pretty_results(ctx, "playing", f"Found **{len(loop)}** result(s) for **{search}**", loop)

    @find.command(name="username", aliases=["name"])
    async def find_name(self, ctx, *, search: str):
        """Find members by username."""
        loop = [f"{i} ({i.id})" for i in ctx.guild.members if search.lower() in i.name.lower() and not i.bot]
        await default.pretty_results(ctx, "name", f"Found **{len(loop)}** result(s) for **{search}**", loop)

    @find.command(name="nickname", aliases=["nick"])
    async def find_nickname(self, ctx, *, search: str):
        """Find members by nickname."""
        loop = [f"{i.nick} | {i} ({i.id})" for i in ctx.guild.members if i.nick and search.lower() in i.nick.lower() and not i.bot]
        await default.pretty_results(ctx, "nickname", f"Found **{len(loop)}** result(s) for **{search}**", loop)

    @find.command(name="id")
    async def find_id(self, ctx, *, search: int):
        """Find members by ID."""
        loop = [f"{i} ({i.id})" for i in ctx.guild.members if str(search) in str(i.id) and not i.bot]
        await default.pretty_results(ctx, "id", f"Found **{len(loop)}** result(s) for `{search}`", loop)

    # ── Massban ────────────────────────────────────────────────────────

    @commands.hybrid_command(name="massban", description="Mass ban multiple users by ID.")
    @commands.guild_only()
    @commands.max_concurrency(1, per=commands.BucketType.user)
    @app_commands.describe(user_ids="Space or comma-separated user IDs to ban", reason="Reason for the ban")
    @mod_check(ban_members=True)
    async def massban(self, ctx: CustomContext, user_ids: str, *, reason: str = None):
        """Mass ban multiple users by ID."""
        member_ids = parse_ids(user_ids)
        if not member_ids:
            return await ctx.send(embed=err("No valid user IDs found. Provide space or comma-separated IDs."), ephemeral=True)

        # Paladin strikes registered individually by on_member_ban listeners after each ban executes.

        embed = discord.Embed(
            title="⚠️  Confirm Mass Ban",
            description=f"You are about to ban **{len(member_ids)} user(s)**.\n**Reason:** {reason or 'No reason provided'}\n\n⚠️ This cannot be undone.",
            colour=COL_CONFIRM,
        )
        embed.set_footer(text="Expires in 30 seconds.")

        if ctx.interaction:
            confirmed, btn = await slash_confirm(ctx.interaction, embed)
            if not confirmed: return
            actor = ctx.guild.get_member(ctx.author.id)
            if not actor or not _still_has_perm(actor, "ban_members"):
                return await btn.response.edit_message(
                    embed=err("❌ You no longer have permission to ban members."), view=None)
            banned = failed = 0
            for mid in member_ids:
                try:
                    await ctx.guild.ban(discord.Object(id=mid), reason=default.responsible(ctx.author, reason))
                    banned += 1
                except Exception:
                    failed += 1
            result = discord.Embed(title="🔨  Mass Ban", colour=COL_MOD)
            result.add_field(name="✅ Banned",     value=str(banned),                   inline=True)
            result.add_field(name="❌ Failed",     value=str(failed),                   inline=True)
            result.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
            result.add_field(name="🛡️ Moderator", value=ctx.author.mention,            inline=True)
            await btn.response.edit_message(embed=result, view=None)
        else:
            confirmed, msg, interaction = await send_confirm(ctx, embed)
            if not confirmed:
                if interaction:
                    await interaction.response.edit_message(
                        embed=discord.Embed(description="❌  Mass ban cancelled.", colour=COL_WARN), view=None)
                return
            if not _still_has_perm(ctx.author, "ban_members"):
                return await interaction.response.edit_message(
                    embed=err("❌ You no longer have permission to ban members."), view=None)
            banned = failed = 0
            for mid in member_ids:
                try:
                    await ctx.guild.ban(discord.Object(id=mid), reason=default.responsible(ctx.author, reason))
                    banned += 1
                except Exception:
                    failed += 1
            result = discord.Embed(title="🔨  Mass Ban", colour=COL_MOD)
            result.add_field(name="✅ Banned",     value=str(banned),                   inline=True)
            result.add_field(name="❌ Failed",     value=str(failed),                   inline=True)
            result.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
            result.add_field(name="🛡️ Moderator", value=ctx.author.mention,            inline=True)
            await interaction.response.edit_message(embed=result, view=None)

    # ── Masskick ───────────────────────────────────────────────────────

    @commands.hybrid_command(name="masskick", description="Mass kick multiple users by ID.")
    @commands.guild_only()
    @commands.max_concurrency(1, per=commands.BucketType.user)
    @app_commands.describe(user_ids="Space or comma-separated user IDs to kick", reason="Reason for the kick")
    @mod_check(kick_members=True)
    async def masskick(self, ctx: CustomContext, user_ids: str, *, reason: str = None):
        """Mass kick multiple users by ID."""
        member_ids = parse_ids(user_ids)
        if not member_ids:
            return await ctx.send(embed=err("No valid user IDs found. Provide space or comma-separated IDs."), ephemeral=True)

        # Paladin strikes registered by on_member_remove audit-log listener after each kick executes.

        embed = discord.Embed(
            title="⚠️  Confirm Mass Kick",
            description=f"You are about to kick **{len(member_ids)} user(s)**.\n**Reason:** {reason or 'No reason provided'}\n\n⚠️ This cannot be undone.",
            colour=COL_CONFIRM,
        )
        embed.set_footer(text="Expires in 30 seconds.")

        if ctx.interaction:
            confirmed, btn = await slash_confirm(ctx.interaction, embed)
            if not confirmed: return
            actor = ctx.guild.get_member(ctx.author.id)
            if not actor or not _still_has_perm(actor, "kick_members"):
                return await btn.response.edit_message(
                    embed=err("❌ You no longer have permission to kick members."), view=None)
            kicked = failed = 0
            for mid in member_ids:
                member = ctx.guild.get_member(mid)
                if member is None:
                    failed += 1
                    continue
                try:
                    await member.kick(reason=default.responsible(ctx.author, reason))
                    kicked += 1
                except Exception:
                    failed += 1
            result = discord.Embed(title="👢  Mass Kick", colour=COL_MOD)
            result.add_field(name="✅ Kicked",     value=str(kicked),                   inline=True)
            result.add_field(name="❌ Failed",     value=str(failed),                   inline=True)
            result.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
            result.add_field(name="🛡️ Moderator", value=ctx.author.mention,            inline=True)
            await btn.response.edit_message(embed=result, view=None)
        else:
            confirmed, msg, interaction = await send_confirm(ctx, embed)
            if not confirmed:
                if interaction:
                    await interaction.response.edit_message(
                        embed=discord.Embed(description="❌  Mass kick cancelled.", colour=COL_WARN), view=None)
                return
            if not _still_has_perm(ctx.author, "kick_members"):
                return await interaction.response.edit_message(
                    embed=err("❌ You no longer have permission to kick members."), view=None)
            kicked = failed = 0
            for mid in member_ids:
                member = ctx.guild.get_member(mid)
                if member is None:
                    failed += 1
                    continue
                try:
                    await member.kick(reason=default.responsible(ctx.author, reason))
                    kicked += 1
                except Exception:
                    failed += 1
            result = discord.Embed(title="👢  Mass Kick", colour=COL_MOD)
            result.add_field(name="✅ Kicked",     value=str(kicked),                   inline=True)
            result.add_field(name="❌ Failed",     value=str(failed),                   inline=True)
            result.add_field(name="📝 Reason",    value=reason or "No reason provided", inline=False)
            result.add_field(name="🛡️ Moderator", value=ctx.author.mention,            inline=True)
            await interaction.response.edit_message(embed=result, view=None)

    # ── Purge User ────────────────────────────────────────────────────

    @commands.hybrid_command(name="purgeuser", description="Purge messages from a specific user in this channel.")
    @commands.guild_only()
    @app_commands.describe(member="User whose messages to delete", amount="Number of messages to scan (max 200)")
    @mod_check(manage_messages=True)
    async def purgeuser(self, ctx: CustomContext, member: discord.Member, amount: int = 50):
        """Purge messages from a specific user in the current channel."""
        if amount < 1 or amount > 200:
            return await ctx.send(embed=err("Amount must be between 1 and 200."), ephemeral=True)

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        deleted = await ctx.channel.purge(
            limit=amount,
            check=lambda m: m.author.id == member.id,
            reason=default.responsible(ctx.author, f"Purge messages from {member}"),
        )

        embed = discord.Embed(
            title="🗑️  User Messages Purged",
            description=f"Deleted **{len(deleted)}** message{'s' if len(deleted) != 1 else ''} from {member.mention}.",
            colour=COL_SUCCESS,
        )
        embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="📺 Channel", value=ctx.channel.mention, inline=True)
        embed.set_footer(text=f"Scanned last {amount} messages")

        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed, delete_after=10)


async def setup(bot):
    await bot.add_cog(Moderator(bot))