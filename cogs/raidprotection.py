import discord
import asyncio
import datetime

from collections import defaultdict, deque
from discord.ext import commands
from discord import app_commands
from utils.default import CustomContext
from utils.data import DiscordBot
from utils import permissions, default
from utils.permissions import OWNERS

# ── Colour palette (mirrors mod.py) ───────────────────────────────────────────

COL_SUCCESS = discord.Colour.green()
COL_ERROR   = discord.Colour.red()
COL_WARN    = discord.Colour.orange()
COL_INFO    = discord.Colour.blurple()
COL_MOD     = discord.Colour.from_str("#E74C3C")
COL_CONFIRM = discord.Colour.from_str("#F39C12")
COL_RAID    = discord.Colour.from_str("#FF4500")   # orange-red for raid alerts

# ── Raid defaults ──────────────────────────────────────────────────────────────

DEFAULT_JOIN_THRESHOLD   = 8         # joins …
DEFAULT_JOIN_WINDOW      = 10        # … within this many seconds → raid detected
DEFAULT_ACTION           = "lockdown"  # "lockdown" | "kick" | "ban"
DEFAULT_MIN_ACCOUNT_AGE  = 7         # days — skip accounts older than this (0 = act on everyone)
DEFAULT_BAN_DELETE_DAYS  = 1         # days of messages wiped on auto-ban (0–7)

# ── Embed helpers ──────────────────────────────────────────────────────────────

def err(text):
    return discord.Embed(description=f"❌  {text}", colour=COL_ERROR)

def ok(text):
    return discord.Embed(description=f"✅  {text}", colour=COL_SUCCESS)


# ── Permission decorator (identical contract to mod.py) ────────────────────────

def mod_check(**required_perms):
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in OWNERS:
            return True
        if ctx.guild and ctx.author.id == ctx.guild.owner_id:
            return True
        guild_perms = ctx.author.guild_permissions
        missing = [p for p in required_perms if not getattr(guild_perms, p, False)]
        if missing:
            names = ", ".join(f"`{p.replace('_', ' ').title()}`" for p in missing)
            embed = discord.Embed(
                description=f"❌ You need the {names} permission{'s' if len(missing) > 1 else ''} to use this.",
                colour=COL_ERROR,
            )
            try:
                if ctx.interaction:
                    await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await ctx.send(embed=embed, ephemeral=True)
            except Exception:
                pass
            return False
        return True
    return commands.check(predicate)


# ── Runtime permission re-check ────────────────────────────────────────────────

def _still_has_perm(member: discord.Member, perm: str) -> bool:
    if member.id in OWNERS:
        return True
    if member.id == member.guild.owner_id:
        return True
    return getattr(member.guild_permissions, perm, False)


# ── Confirmation views (same pattern as mod.py) ────────────────────────────────

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


async def send_confirm(ctx: CustomContext, embed: discord.Embed):
    view = ConfirmView(ctx.author.id)
    msg  = await ctx.send(embed=embed, view=view)
    await view.wait()
    try:
        await msg.edit(view=view)
    except discord.NotFound:
        pass
    if view.confirmed is None:
        await msg.edit(
            embed=discord.Embed(description="⏳  Action cancelled — timed out.", colour=COL_WARN),
            view=view,
        )
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


# ── Cog ────────────────────────────────────────────────────────────────────────

class RaidProtection(commands.Cog):
    """
    Automatic and manual raid protection.

    Auto-detection: tracks join timestamps per guild; when the join rate
    exceeds the configured threshold the bot triggers the configured action
    (lockdown / kick / ban) and fires an alert embed to the log channel.

    Manual controls: raidmode, lockdown, unlockdown, raidstatus, raidconfig.

    Per-guild state is held in-memory (resets on restart).  Hook the
    _cfg() getter into your DB layer to make settings persistent.
    """

    def __init__(self, bot):
        self.bot: DiscordBot = bot

        # join-rate tracking: guild_id → deque of UTC timestamps
        self._join_log: dict[int, deque] = defaultdict(deque)

        # manual / auto raid-mode flag: guild_id → bool
        self._raid_mode: dict[int, bool] = {}

        # saved channel overwrites for lockdown restore: guild_id → {ch_id → overwrite}
        self._saved_overwrites: dict[int, dict[int, discord.PermissionOverwrite]] = {}

        # per-guild config (in-memory; swap for DB calls as needed)
        # shape: { threshold, window, action, log_channel_id }
        self._cfg_store: dict[int, dict] = {}

        # debounce: prevent firing the auto-raid logic multiple times at once
        self._raid_lock: set[int] = set()

    # ── Config helpers ─────────────────────────────────────────────────

    def _cfg(self, guild_id: int) -> dict:
        """Return config for a guild, falling back to defaults."""
        return self._cfg_store.get(guild_id, {
            "threshold":        DEFAULT_JOIN_THRESHOLD,
            "window":           DEFAULT_JOIN_WINDOW,
            "action":           DEFAULT_ACTION,
            "min_account_age":  DEFAULT_MIN_ACCOUNT_AGE,
            "ban_delete_days":  DEFAULT_BAN_DELETE_DAYS,
            "log_channel_id":   None,
        })

    def _set_cfg(self, guild_id: int, **kwargs):
        cfg = self._cfg(guild_id).copy()
        cfg.update(kwargs)
        self._cfg_store[guild_id] = cfg

    def _is_raid_mode(self, guild_id: int) -> bool:
        return self._raid_mode.get(guild_id, False)

    # ── Alert helper ───────────────────────────────────────────────────

    async def _send_alert(self, guild: discord.Guild, embed: discord.Embed):
        """Push an embed to the configured log channel, if any."""
        log_ch_id = self._cfg(guild.id).get("log_channel_id")
        if not log_ch_id:
            return
        ch = guild.get_channel(log_ch_id)
        if ch and ch.permissions_for(guild.me).send_messages:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass

    # ── Lockdown helpers ───────────────────────────────────────────────

    async def _lock_channel(
        self,
        channel: discord.TextChannel | discord.VoiceChannel | discord.ForumChannel,
        guild: discord.Guild,
        save: bool = True,
    ):
        """
        Remove Send Messages / Speak for @everyone on one channel.
        Saves original overwrite so unlockdown can restore it exactly.
        """
        everyone = guild.default_role
        current  = channel.overwrites_for(everyone)

        if save:
            self._saved_overwrites.setdefault(guild.id, {})[channel.id] = current.copy()

        new_ow = current.copy()
        if isinstance(channel, discord.VoiceChannel):
            new_ow.speak = False
        else:
            new_ow.send_messages = False
            new_ow.send_messages_in_threads = False
            new_ow.add_reactions = False

        try:
            await channel.set_permissions(everyone, overwrite=new_ow, reason="Raid protection lockdown")
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _unlock_channel(
        self,
        channel: discord.TextChannel | discord.VoiceChannel | discord.ForumChannel,
        guild: discord.Guild,
    ):
        """Restore the @everyone overwrite that existed before lockdown."""
        everyone = guild.default_role
        saved    = self._saved_overwrites.get(guild.id, {}).pop(channel.id, None)

        if saved is None:
            # Nothing saved — just reset the deny so we don't leave it broken
            saved = discord.PermissionOverwrite()

        try:
            if saved.is_empty():
                await channel.set_permissions(everyone, overwrite=None, reason="Raid protection lift")
            else:
                await channel.set_permissions(everyone, overwrite=saved, reason="Raid protection lift")
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── Auto-raid detection (listener) ────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        cfg   = self._cfg(guild.id)

        now       = datetime.datetime.utcnow().timestamp()
        threshold = cfg["threshold"]
        window    = cfg["window"]

        q = self._join_log[guild.id]
        q.append(now)

        # Trim events outside the rolling window
        while q and now - q[0] > window:
            q.popleft()

        # If already in raid mode, apply action immediately to this joiner
        if self._is_raid_mode(guild.id):
            await self._apply_action_to_member(member, cfg["action"], cfg)
            return

        # Threshold crossed → engage raid mode
        if len(q) >= threshold and guild.id not in self._raid_lock:
            self._raid_lock.add(guild.id)
            try:
                await self._engage_raid_mode(guild, trigger_member=member, cfg=cfg)
            finally:
                self._raid_lock.discard(guild.id)

    async def _apply_action_to_member(self, member: discord.Member, action: str, cfg: dict):
        """
        Kick or ban a single member as part of raid response.

        Respects:
          - min_account_age : skip members whose Discord account is older than N days
                              (0 = act on everyone regardless of age)
          - ban_delete_days : how many days of messages to wipe on ban (0–7)
        """
        if action not in ("kick", "ban"):
            return  # lockdown action never touches individual members

        min_age  = cfg.get("min_account_age", DEFAULT_MIN_ACCOUNT_AGE)
        del_days = cfg.get("ban_delete_days",  DEFAULT_BAN_DELETE_DAYS)

        # Age filter — skip established accounts if min_account_age > 0
        if min_age > 0:
            account_age_days = (datetime.datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
            if account_age_days > min_age:
                return  # account is older than threshold → not a fresh raider, skip

        reason = "Raid protection — automatic action"
        try:
            if action == "kick":
                await member.kick(reason=reason)
            elif action == "ban":
                await member.ban(reason=reason, delete_message_days=max(0, min(del_days, 7)))
        except Exception:
            pass

    async def _engage_raid_mode(
        self,
        guild: discord.Guild,
        trigger_member: discord.Member | None = None,
        cfg: dict | None = None,
    ):
        """
        Flip raid-mode on, execute the configured action, and fire an alert.
        Called both automatically (from on_member_join) and manually (raidmode on).
        """
        if cfg is None:
            cfg = self._cfg(guild.id)

        self._raid_mode[guild.id] = True
        action = cfg["action"]

        locked = 0
        acted  = 0

        if action == "lockdown":
            channels = [
                c for c in guild.channels
                if isinstance(c, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel))
                and c.permissions_for(guild.me).manage_channels
            ]
            await asyncio.gather(*[self._lock_channel(c, guild) for c in channels])
            locked = len(channels)

        elif action in ("kick", "ban"):
            # Apply to every member who joined within the detection window
            now    = datetime.datetime.utcnow().timestamp()
            window = cfg["window"]
            recent = [
                m for m in guild.members
                if m.joined_at
                and now - m.joined_at.timestamp() <= window
                and not m.bot
                and not m.guild_permissions.administrator
            ]
            await asyncio.gather(*[self._apply_action_to_member(m, action, cfg) for m in recent])
            acted = len(recent)

        # Alert embed
        alert = discord.Embed(
            title="🚨  RAID DETECTED — Protection Engaged",
            colour=COL_RAID,
            timestamp=datetime.datetime.utcnow(),
        )
        alert.add_field(name="⚙️ Action",    value=action.title(), inline=True)
        if trigger_member:
            alert.add_field(name="🔔 Trigger",  value=f"{trigger_member.mention} `{trigger_member}`", inline=True)
        if action == "lockdown":
            alert.add_field(name="🔒 Channels Locked", value=str(locked), inline=True)
        elif action in ("kick", "ban"):
            alert.add_field(name=f"{'👢' if action == 'kick' else '🔨'} Members {action.title()}ed",
                            value=str(acted), inline=True)
        alert.set_footer(text=f"Guild ID: {guild.id}")
        await self._send_alert(guild, alert)

    async def _disengage_raid_mode(self, guild: discord.Guild):
        """Flip raid-mode off and restore any locked channels."""
        self._raid_mode[guild.id] = False
        self._join_log[guild.id].clear()

        saved = self._saved_overwrites.get(guild.id, {})
        if saved:
            channels = [guild.get_channel(ch_id) for ch_id in list(saved.keys())]
            channels = [c for c in channels if c is not None]
            await asyncio.gather(*[self._unlock_channel(c, guild) for c in channels])

        alert = discord.Embed(
            title="✅  Raid Mode Disengaged",
            description="Channels restored. Normal operation resumed.",
            colour=COL_SUCCESS,
            timestamp=datetime.datetime.utcnow(),
        )
        alert.set_footer(text=f"Guild ID: {guild.id}")
        await self._send_alert(guild, alert)

    # ── raidmode ──────────────────────────────────────────────────────

    @commands.hybrid_command(name="raidmode", description="Manually enable or disable raid protection mode.")
    @commands.guild_only()
    @app_commands.describe(state="on or off")
    @mod_check(manage_guild=True)
    async def raidmode(self, ctx: CustomContext, state: str):
        """Manually toggle raid mode on or off."""
        state = state.strip().lower()
        if state not in ("on", "off"):
            return await ctx.send(embed=err("Provide `on` or `off`."), ephemeral=True)

        currently_on = self._is_raid_mode(ctx.guild.id)

        if state == "on":
            if currently_on:
                return await ctx.send(embed=err("Raid mode is already **enabled**."), ephemeral=True)

            embed = discord.Embed(
                title="⚠️  Enable Raid Mode?",
                description=(
                    "This will trigger the configured action:\n"
                    f"**{self._cfg(ctx.guild.id)['action'].title()}**\n\n"
                    "All locked channels will be restored when you run `/raidmode off`."
                ),
                colour=COL_CONFIRM,
            )
            embed.set_footer(text="Expires in 30 seconds.")

            if ctx.interaction:
                confirmed, btn = await slash_confirm(ctx.interaction, embed)
                if not confirmed: return
                if not _still_has_perm(ctx.author, "manage_guild"):
                    return await btn.response.edit_message(
                        embed=err("You no longer have the required permission."), view=None)
                await self._engage_raid_mode(ctx.guild)
                result = discord.Embed(
                    title="🚨  Raid Mode Enabled",
                    description=f"Action `{self._cfg(ctx.guild.id)['action']}` applied. Run `/raidmode off` to lift.",
                    colour=COL_RAID,
                )
                result.add_field(name="🛡️ Activated by", value=ctx.author.mention, inline=True)
                await btn.response.edit_message(embed=result, view=None)
            else:
                confirmed, msg, interaction = await send_confirm(ctx, embed)
                if not confirmed:
                    if interaction:
                        await interaction.response.edit_message(
                            embed=discord.Embed(description="❌  Cancelled.", colour=COL_WARN), view=None)
                    return
                if not _still_has_perm(ctx.author, "manage_guild"):
                    return await interaction.response.edit_message(
                        embed=err("You no longer have the required permission."), view=None)
                await self._engage_raid_mode(ctx.guild)
                result = discord.Embed(
                    title="🚨  Raid Mode Enabled",
                    description=f"Action `{self._cfg(ctx.guild.id)['action']}` applied. Run `/raidmode off` to lift.",
                    colour=COL_RAID,
                )
                result.add_field(name="🛡️ Activated by", value=ctx.author.mention, inline=True)
                await interaction.response.edit_message(embed=result, view=None)

        else:  # off
            if not currently_on:
                return await ctx.send(embed=err("Raid mode is already **disabled**."), ephemeral=True)

            await self._disengage_raid_mode(ctx.guild)
            result = discord.Embed(
                title="✅  Raid Mode Disabled",
                description="Channels have been restored. Normal operation resumed.",
                colour=COL_SUCCESS,
            )
            result.add_field(name="🛡️ Deactivated by", value=ctx.author.mention, inline=True)

            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=result, ephemeral=False)
            else:
                await ctx.send(embed=result)

    # ── lockdown ──────────────────────────────────────────────────────

    @commands.hybrid_command(name="lockdown", aliases=["ld"], description="Lock a channel (or all channels) during an emergency.")
    @commands.guild_only()
    @app_commands.describe(channel="Channel to lock — omit to lock ALL channels")
    @mod_check(manage_channels=True)
    async def lockdown(self, ctx: CustomContext, channel: discord.TextChannel = None):
        """Lock one channel, or every channel if none is specified."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        if channel:
            await self._lock_channel(channel, ctx.guild)
            embed = discord.Embed(
                title="🔒  Channel Locked",
                description=f"{channel.mention} has been locked for `@everyone`.",
                colour=COL_MOD,
            )
            embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
            embed.set_footer(text="Use /unlockdown to restore.")
        else:
            channels = [
                c for c in ctx.guild.channels
                if isinstance(c, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel))
                and c.permissions_for(ctx.guild.me).manage_channels
            ]
            await asyncio.gather(*[self._lock_channel(c, ctx.guild) for c in channels])
            embed = discord.Embed(
                title="🔒  Server Lockdown Active",
                description=f"**{len(channels)}** channel(s) locked for `@everyone`.",
                colour=COL_MOD,
            )
            embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
            embed.set_footer(text="Use /unlockdown to restore all channels.")

        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, ephemeral=False)
        else:
            await ctx.send(embed=embed)

    # ── unlockdown ────────────────────────────────────────────────────

    @commands.hybrid_command(name="unlockdown", aliases=["uld"], description="Restore a locked channel (or all channels).")
    @commands.guild_only()
    @app_commands.describe(channel="Channel to unlock — omit to unlock ALL channels")
    @mod_check(manage_channels=True)
    async def unlockdown(self, ctx: CustomContext, channel: discord.TextChannel = None):
        """Unlock one channel, or every saved channel if none is specified."""
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        if channel:
            await self._unlock_channel(channel, ctx.guild)
            embed = discord.Embed(
                title="🔓  Channel Unlocked",
                description=f"{channel.mention} has been restored.",
                colour=COL_SUCCESS,
            )
            embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
        else:
            saved = self._saved_overwrites.get(ctx.guild.id, {})
            if not saved:
                return await (ctx.interaction.followup.send if ctx.interaction else ctx.send)(
                    embed=err("No saved overwrites found. Either no lockdown was active, or it was already lifted."),
                    ephemeral=True,
                )
            channels = [ctx.guild.get_channel(cid) for cid in list(saved.keys())]
            channels = [c for c in channels if c]
            await asyncio.gather(*[self._unlock_channel(c, ctx.guild) for c in channels])
            embed = discord.Embed(
                title="🔓  Lockdown Lifted",
                description=f"**{len(channels)}** channel(s) restored.",
                colour=COL_SUCCESS,
            )
            embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)

        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, ephemeral=False)
        else:
            await ctx.send(embed=embed)

    # ── raidstatus ────────────────────────────────────────────────────

    @commands.hybrid_command(name="raidstatus", description="Show current raid protection status and config.")
    @commands.guild_only()
    @mod_check(manage_guild=True)
    async def raidstatus(self, ctx: CustomContext):
        """Display raid mode status and current settings."""
        cfg        = self._cfg(ctx.guild.id)
        active     = self._is_raid_mode(ctx.guild.id)
        log_ch     = ctx.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
        locked_cnt = len(self._saved_overwrites.get(ctx.guild.id, {}))
        recent     = len(self._join_log.get(ctx.guild.id, []))

        embed = discord.Embed(
            title="🛡️  Raid Protection Status",
            colour=COL_RAID if active else COL_INFO,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(
            name="🚨 Raid Mode",
            value="**ACTIVE** 🔴" if active else "Inactive 🟢",
            inline=False,
        )
        embed.add_field(name="⚙️ Action",        value=cfg["action"].title(),         inline=True)
        embed.add_field(name="📊 Threshold",     value=f"{cfg['threshold']} joins",   inline=True)
        embed.add_field(name="⏱️ Window",        value=f"{cfg['window']}s",           inline=True)
        embed.add_field(name="📺 Log Channel",   value=log_ch.mention if log_ch else "Not set", inline=True)
        embed.add_field(name="🔒 Locked Channels", value=str(locked_cnt),             inline=True)
        embed.add_field(name="👥 Recent Joins",  value=f"{recent} (in window)",       inline=True)
        # kick/ban-specific settings
        age = cfg.get("min_account_age", DEFAULT_MIN_ACCOUNT_AGE)
        embed.add_field(
            name="🗓️ Min Account Age",
            value=f"{age}d (skip accounts older than this)" if age > 0 else "Disabled (act on all)",
            inline=False,
        )
        embed.add_field(
            name="🗑️ Ban Delete Days",
            value=f"{cfg.get('ban_delete_days', DEFAULT_BAN_DELETE_DAYS)}d of messages wiped",
            inline=True,
        )
        embed.set_footer(text=f"Guild ID: {ctx.guild.id}")

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

    # ── raidconfig ────────────────────────────────────────────────────

    @commands.hybrid_command(name="raidconfig", description="Configure raid protection settings.")
    @commands.guild_only()
    @app_commands.describe(
        threshold="Number of joins that trigger raid mode (e.g. 8)",
        window="Rolling time window in seconds (e.g. 10)",
        action="Response action: lockdown, kick, or ban",
        log_channel="Channel to send raid alerts to",
        min_account_age="Only kick/ban accounts younger than this many days (0 = everyone)",
        ban_delete_days="Days of messages to delete when auto-banning (0–7)",
    )
    @mod_check(manage_guild=True)
    async def raidconfig(
        self,
        ctx: CustomContext,
        threshold: int = None,
        window: int = None,
        action: str = None,
        log_channel: discord.TextChannel = None,
        min_account_age: int = None,
        ban_delete_days: int = None,
    ):
        """
        Update one or more raid protection settings.
        Run with no arguments to see current config.
        """
        if all(v is None for v in (threshold, window, action, log_channel, min_account_age, ban_delete_days)):
            return await self.raidstatus(ctx)

        errors = []

        if threshold is not None:
            if threshold < 2 or threshold > 100:
                errors.append("`threshold` must be between 2 and 100.")
            else:
                self._set_cfg(ctx.guild.id, threshold=threshold)

        if window is not None:
            if window < 3 or window > 300:
                errors.append("`window` must be between 3 and 300 seconds.")
            else:
                self._set_cfg(ctx.guild.id, window=window)

        if action is not None:
            action = action.lower()
            if action not in ("lockdown", "kick", "ban"):
                errors.append("`action` must be `lockdown`, `kick`, or `ban`.")
            else:
                self._set_cfg(ctx.guild.id, action=action)

        if log_channel is not None:
            if not log_channel.permissions_for(ctx.guild.me).send_messages:
                errors.append(f"I cannot send messages in {log_channel.mention}.")
            else:
                self._set_cfg(ctx.guild.id, log_channel_id=log_channel.id)

        if min_account_age is not None:
            if min_account_age < 0 or min_account_age > 365:
                errors.append("`min_account_age` must be between 0 and 365 days.")
            else:
                self._set_cfg(ctx.guild.id, min_account_age=min_account_age)

        if ban_delete_days is not None:
            if ban_delete_days < 0 or ban_delete_days > 7:
                errors.append("`ban_delete_days` must be between 0 and 7.")
            else:
                self._set_cfg(ctx.guild.id, ban_delete_days=ban_delete_days)

        if errors:
            combined = "\n".join(f"• {e}" for e in errors)
            embed = discord.Embed(
                title="⚠️  Config Errors",
                description=combined,
                colour=COL_WARN,
            )
            return await (
                ctx.interaction.response.send_message(embed=embed, ephemeral=True)
                if ctx.interaction else ctx.send(embed=embed, ephemeral=True)
            )

        cfg    = self._cfg(ctx.guild.id)
        log_ch = ctx.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
        age    = cfg.get("min_account_age", DEFAULT_MIN_ACCOUNT_AGE)
        embed  = discord.Embed(title="✅  Raid Config Updated", colour=COL_SUCCESS)
        embed.add_field(name="⚙️ Action",         value=cfg["action"].title(),        inline=True)
        embed.add_field(name="📊 Threshold",      value=f"{cfg['threshold']} joins",  inline=True)
        embed.add_field(name="⏱️ Window",         value=f"{cfg['window']}s",          inline=True)
        embed.add_field(name="📺 Log",            value=log_ch.mention if log_ch else "Not set", inline=True)
        embed.add_field(name="🗓️ Min Acct Age",  value=f"{age}d" if age > 0 else "Disabled", inline=True)
        embed.add_field(name="🗑️ Ban Del Days",  value=str(cfg.get("ban_delete_days", DEFAULT_BAN_DELETE_DAYS)), inline=True)
        embed.set_footer(text=f"Updated by {ctx.author}")

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)


# ── Setup ──────────────────────────────────────────────────────────────────────

async def setup(bot):
    await bot.add_cog(RaidProtection(bot))
