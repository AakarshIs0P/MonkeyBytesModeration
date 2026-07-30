"""Auditable, server-scoped invite tracking with suspected-alt review."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import discord
from discord.ext import commands, tasks

from utils.data import DiscordBot
from utils.default import CustomContext
from utils.json_store import AtomicJSONStore

log = logging.getLogger("bot.invite_tracker")
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "invite_tracker.json"
ALERT_FILE = ROOT / "data" / "paladin_alertnuke.json"
SCHEMA_VERSION = 1
DEFAULT_ALT_AGE_DAYS = 14
COLOUR = discord.Colour.from_str("#5865F2")


def default_document() -> dict:
    return {"schema_version": SCHEMA_VERSION, "guilds": {}}


def default_guild() -> dict:
    return {
        "settings": {
            "enabled": False,
            "alt_age_days": DEFAULT_ALT_AGE_DAYS,
            "log_channel_id": None,
            "dm_alerts_enabled": False,
            "join_messages_enabled": False,
            "join_channel_id": None,
        },
        "baseline_started_at": None,
        "invite_snapshots": {},
        "vanity_snapshot": None,
        "members": {},
        "adjustments": {},
        "audit_log": [],
    }


def unknown_attribution(confidence: str = "unknown") -> dict:
    return {
        "source": "unknown",
        "confidence": confidence,
        "invite_code": None,
        "inviter_id": None,
    }


def detect_invite_use(before: dict, after: dict, vanity_before, vanity_after) -> dict:
    candidates = []
    for code, current in after.items():
        old_uses = int(before.get(code, {}).get("uses") or 0)
        new_uses = int(current.get("uses") or 0)
        if new_uses > old_uses:
            candidates.append((code, current, new_uses - old_uses))

    disappeared = []
    for code, old in before.items():
        if code in after:
            continue
        max_uses = int(old.get("max_uses") or 0)
        uses = int(old.get("uses") or 0)
        if max_uses > 0 and uses + 1 >= max_uses:
            disappeared.append((code, old, 1))

    vanity_delta = (
        vanity_before is not None
        and vanity_after is not None
        and int(vanity_after) > int(vanity_before)
    )
    plausible = candidates + disappeared
    if len(plausible) == 1 and plausible[0][2] == 1 and not vanity_delta:
        code, invite, _ = plausible[0]
        return {
            "source": "invite",
            "confidence": "exact",
            "invite_code": code,
            "inviter_id": invite.get("inviter_id"),
        }
    if not plausible and vanity_delta and int(vanity_after) - int(vanity_before) == 1:
        return {
            "source": "vanity",
            "confidence": "exact",
            "invite_code": None,
            "inviter_id": None,
        }
    if plausible or vanity_delta:
        return unknown_attribution("ambiguous")
    return unknown_attribution()


def classify_account(created_at: datetime, joined_at: datetime, alt_age_days: int) -> str:
    if alt_age_days <= 0:
        return "valid"
    return (
        "suspected_alt"
        if (joined_at - created_at).total_seconds() < alt_age_days * 86400
        else "valid"
    )


def make_join_record(
    member_id: int,
    inviter_id: int | None,
    invite_code: str | None,
    source: str,
    confidence: str,
    joined_at: int,
    account_created_at: int,
    account_age_seconds: int,
    classification: str,
) -> dict:
    countable = confidence == "exact" and inviter_id is not None
    suspected = classification == "suspected_alt"
    return {
        "member_id": member_id,
        "inviter_id": inviter_id,
        "invite_code": invite_code,
        "source": source,
        "confidence": confidence,
        "joined_at": joined_at,
        "left_at": None,
        "account_created_at": account_created_at,
        "account_age_seconds": account_age_seconds,
        "classification": classification,
        "review_status": "pending" if suspected else "not_required",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_reason": None,
        "counted": bool(countable and not suspected and classification == "valid"),
    }


def append_audit(
    guild_data: dict,
    event: str,
    actor_id: int | None,
    target_id: int | None = None,
    details: dict | None = None,
    timestamp: int | None = None,
) -> None:
    guild_data["audit_log"].append(
        {
            "event": event,
            "actor_id": actor_id,
            "target_id": target_id,
            "details": copy.deepcopy(details) if details else {},
            "timestamp": (
                timestamp
                if timestamp is not None
                else int(datetime.now(timezone.utc).timestamp())
            ),
        }
    )
    guild_data["audit_log"][:] = guild_data["audit_log"][-5000:]


def record_join(guild_data: dict, record: dict) -> dict:
    key = str(record["member_id"])
    existing = guild_data["members"].get(key)
    if existing:
        existing["left_at"] = None
        append_audit(
            guild_data,
            "rejoin_observed",
            None,
            record["member_id"],
            {
                "source": record["source"],
                "invite_code": record["invite_code"],
                "inviter_id": record["inviter_id"],
            },
            record["joined_at"],
        )
        return {"created": False, "record": existing}
    guild_data["members"][key] = record
    append_audit(guild_data, "join", None, record["member_id"], record, record["joined_at"])
    return {"created": True, "record": record}


def record_leave(guild_data: dict, member_id: int, left_at: int) -> bool:
    record = guild_data["members"].get(str(member_id))
    if not record or record.get("left_at") is not None:
        return False
    record["left_at"] = left_at
    append_audit(guild_data, "leave", None, member_id, timestamp=left_at)
    return True


def reset_member(guild_data: dict, member_id: int, actor_id: int | None = None) -> bool:
    if guild_data["members"].pop(str(member_id), None) is None:
        return False
    append_audit(guild_data, "member_reset", actor_id, member_id)
    return True


def review_alt(
    record: dict,
    approve: bool,
    reviewer_id: int,
    reason: str | None,
    reviewed_at: int,
) -> None:
    if record.get("review_status") not in {"pending", "denied", "approved"}:
        raise ValueError("This member is not a suspected-alt review record.")
    record["review_status"] = "approved" if approve else "denied"
    record["classification"] = "valid" if approve else "suspected_alt"
    record["counted"] = bool(
        approve and record.get("confidence") == "exact" and record.get("inviter_id")
    )
    record["reviewed_by"] = reviewer_id
    record["reviewed_at"] = reviewed_at
    record["review_reason"] = reason


def calculate_stats(records: Iterable[dict], inviter_id: int, bonus: int = 0) -> dict:
    valid = alts = left = 0
    for record in records:
        if record.get("inviter_id") != inviter_id or record.get("confidence") != "exact":
            continue
        if record.get("counted"):
            valid += 1
            if record.get("left_at") is not None:
                left += 1
        elif (
            record.get("classification") == "suspected_alt"
            and record.get("review_status") in {"pending", "denied"}
        ):
            alts += 1
    return {
        "valid": valid,
        "alts": alts,
        "left": left,
        "bonus": int(bonus),
        "net": valid + int(bonus) - left,
    }


def normalize_invites(invites) -> dict:
    return {
        invite.code: {
            "uses": int(invite.uses or 0),
            "max_uses": int(invite.max_uses or 0),
            "inviter_id": invite.inviter.id if invite.inviter else None,
            "channel_id": invite.channel.id if invite.channel else None,
        }
        for invite in invites
    }


def update_setting(settings: dict, name: str, value) -> None:
    if name == "alt_age_days":
        value = int(value)
        if not 0 <= value <= 365:
            raise ValueError("Alt age must be between 0 and 365 days.")
    elif name in {"enabled", "dm_alerts_enabled", "join_messages_enabled"}:
        if not isinstance(value, bool):
            raise ValueError("Toggle value must be boolean.")
    elif name in {"log_channel_id", "join_channel_id"}:
        if value is not None and int(value) <= 0:
            raise ValueError("Channel ID must be positive.")
        value = int(value) if value is not None else None
    else:
        raise ValueError("Unknown invite tracker setting.")
    settings[name] = value


def get_alert_recipients(paladin_cog, guild_id: int) -> list[int]:
    if paladin_cog and hasattr(paladin_cog, "get_alert_subscribers"):
        return list(paladin_cog.get_alert_subscribers(guild_id))
    try:
        data = json.loads(ALERT_FILE.read_text(encoding="utf-8"))
        return [int(uid) for uid in data.get(str(guild_id), [])]
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def _on_off(raw: str) -> bool:
    lowered = raw.lower()
    if lowered in {"on", "yes", "true", "1", "enable", "enabled"}:
        return True
    if lowered in {"off", "no", "false", "0", "disable", "disabled"}:
        return False
    raise ValueError("Use `on` or `off`.")


class ResetConfirmView(discord.ui.View):
    def __init__(self, cog: "InviteTracker", guild_id: int, invoker_id: int):
        super().__init__(timeout=30)
        self.cog = cog
        self.guild_id = guild_id
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the invoking administrator can use this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Reset Server Data", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.store.data["guilds"][str(self.guild_id)] = default_guild()
        self.cog.store.mark_dirty()
        await self.cog.store.flush()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="✅ Invite tracker data reset.", embed=None, view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Reset cancelled.", embed=None, view=self)
        self.stop()


class InviteTracker(commands.Cog, name="InviteTracker"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self.store = AtomicJSONStore(DATA_FILE, default_document)
        self._join_locks = defaultdict(asyncio.Lock)

    async def cog_load(self):
        await self.store.load()
        self.store.data.setdefault("schema_version", SCHEMA_VERSION)
        self.store.data.setdefault("guilds", {})
        self.flush_loop.start()

    def cog_unload(self):
        self.flush_loop.cancel()
        try:
            asyncio.get_running_loop().create_task(self.store.flush(force=True))
        except RuntimeError:
            pass

    @tasks.loop(seconds=30)
    async def flush_loop(self):
        await self.store.flush()

    def _guild_data(self, guild_id: int) -> dict:
        guilds = self.store.data.setdefault("guilds", {})
        if str(guild_id) not in guilds:
            guilds[str(guild_id)] = default_guild()
            self.store.mark_dirty()
        data = guilds[str(guild_id)]
        defaults = default_guild()
        for key, value in defaults.items():
            data.setdefault(key, value)
        for key, value in defaults["settings"].items():
            data["settings"].setdefault(key, value)
        return data

    async def _fetch_snapshot(self, guild: discord.Guild) -> tuple[dict, int | None]:
        invites = normalize_invites(await guild.invites())
        vanity = None
        if "VANITY_URL" in guild.features:
            try:
                vanity_invite = await guild.vanity_invite()
                vanity = int(vanity_invite.uses or 0)
            except (discord.Forbidden, discord.HTTPException):
                pass
        return invites, vanity

    async def _resync_guild(self, guild: discord.Guild, actor_id: int | None = None):
        invites, vanity = await self._fetch_snapshot(guild)
        data = self._guild_data(guild.id)
        data["invite_snapshots"] = invites
        data["vanity_snapshot"] = vanity
        data["baseline_started_at"] = int(discord.utils.utcnow().timestamp())
        append_audit(data, "resync", actor_id)
        self.store.mark_dirty()
        await self.store.flush()

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed):
        channel_id = self._guild_data(guild.id)["settings"].get("log_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel:
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Could not send invite tracker log in guild %s", guild.id)

    def _alt_embed(self, guild: discord.Guild, member, record: dict) -> discord.Embed:
        inviter = guild.get_member(record.get("inviter_id")) if record.get("inviter_id") else None
        age_days = record["account_age_seconds"] / 86400
        embed = discord.Embed(
            title=f"⚠️ Suspected Alt Joined — {guild.name}",
            description=f"{member.mention} (`{member.id}`) was recorded but is **not counted**.",
            colour=discord.Colour.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Account Age", value=f"{age_days:.2f} days", inline=True)
        embed.add_field(name="Inviter", value=inviter.mention if inviter else "Unknown", inline=True)
        embed.add_field(name="Invite", value=f"`{record.get('invite_code') or 'unknown'}`", inline=True)
        embed.add_field(name="Review", value=f"`!invitealt approve {member.id} [reason]`\n`!invitealt deny {member.id} [reason]`", inline=False)
        return embed

    async def _send_alt_dm_alerts(self, guild: discord.Guild, member, record: dict):
        if not self._guild_data(guild.id)["settings"].get("dm_alerts_enabled"):
            return
        recipients = get_alert_recipients(self.bot.get_cog("AntiNuke"), guild.id)
        embed = self._alt_embed(guild, member, record)
        for uid in recipients:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                await user.send(embed=embed)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                log.info("Invite alt DM unavailable for user %s in guild %s", uid, guild.id)

    async def _send_join_message(self, guild: discord.Guild, member, record: dict):
        settings = self._guild_data(guild.id)["settings"]
        if not settings.get("join_messages_enabled"):
            return
        channel_id = settings.get("join_channel_id") or settings.get("log_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not channel:
            return
        if record["classification"] == "suspected_alt":
            text = f"Welcome {member.mention}! Your join was recorded and is awaiting staff review."
        elif record.get("inviter_id"):
            stats = self._stats(guild.id, record["inviter_id"])
            text = f"Welcome {member.mention}! Invited by <@{record['inviter_id']}> (net: **{stats['net']}**)."
        else:
            text = f"Welcome {member.mention}!"
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True))
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild):
        if self._guild_data(guild.id)["settings"]["enabled"]:
            try:
                await self._resync_guild(guild)
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Invite baseline unavailable in guild %s", guild.id)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        data = self._guild_data(invite.guild.id)
        if data["settings"]["enabled"]:
            try:
                await self._resync_guild(invite.guild)
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        # Keep deleted invites until the next join so one-use links remain attributable.
        return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        data = self._guild_data(member.guild.id)
        if not data["settings"]["enabled"]:
            return
        async with self._join_locks[member.guild.id]:
            before = data["invite_snapshots"]
            vanity_before = data.get("vanity_snapshot")
            try:
                after, vanity_after = await self._fetch_snapshot(member.guild)
                attribution = detect_invite_use(before, after, vanity_before, vanity_after)
                data["invite_snapshots"] = after
                data["vanity_snapshot"] = vanity_after
            except (discord.Forbidden, discord.HTTPException):
                attribution = unknown_attribution()
            joined = discord.utils.utcnow()
            classification = (
                "unknown"
                if member.bot
                else classify_account(
                    member.created_at,
                    joined,
                    data["settings"]["alt_age_days"],
                )
            )
            age = max(0, int((joined - member.created_at).total_seconds()))
            record = make_join_record(
                member.id,
                attribution["inviter_id"],
                attribution["invite_code"],
                attribution["source"],
                attribution["confidence"],
                int(joined.timestamp()),
                int(member.created_at.timestamp()),
                age,
                classification,
            )
            result = record_join(data, record)
            self.store.mark_dirty()
            await self.store.flush()
            stored = result["record"]
            if result["created"] and classification == "suspected_alt":
                embed = self._alt_embed(member.guild, member, stored)
                await self._send_log(member.guild, embed)
                await self._send_alt_dm_alerts(member.guild, member, stored)
            elif result["created"] and attribution["confidence"] != "exact":
                await self._send_log(
                    member.guild,
                    discord.Embed(
                        title="Invite Attribution Unavailable",
                        description=f"Stored {member.mention}'s join as `{attribution['confidence']}`; no inviter was credited.",
                        colour=discord.Colour.orange(),
                    ),
                )
            await self._send_join_message(member.guild, member, stored)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        data = self._guild_data(member.guild.id)
        if record_leave(data, member.id, int(discord.utils.utcnow().timestamp())):
            self.store.mark_dirty()
            await self.store.flush()

    def _stats(self, guild_id: int, user_id: int) -> dict:
        data = self._guild_data(guild_id)
        bonus = int(data["adjustments"].get(str(user_id), 0))
        return calculate_stats(data["members"].values(), user_id, bonus)

    def _stats_embed(self, guild, member) -> discord.Embed:
        stats = self._stats(guild.id, member.id)
        embed = discord.Embed(title=f"🔗 Invite Stats — {member}", colour=COLOUR)
        for label, key in [("Valid", "valid"), ("Suspected Alts", "alts"), ("Left", "left"), ("Bonus", "bonus"), ("Net", "net")]:
            embed.add_field(name=label, value=f"`{stats[key]:,}`", inline=True)
        embed.set_footer(text="Net = Valid + Bonus - Left")
        return embed

    @commands.hybrid_command(name="invites")
    @commands.guild_only()
    async def invites(self, ctx: CustomContext, member: discord.Member = None):
        """Show invite totals for a member."""
        await ctx.send(embed=self._stats_embed(ctx.guild, member or ctx.author))

    @commands.hybrid_command(name="invitedby")
    @commands.guild_only()
    async def invitedby(self, ctx: CustomContext, member: discord.Member = None):
        """Show who invited a member and the attribution status."""
        member = member or ctx.author
        record = self._guild_data(ctx.guild.id)["members"].get(str(member.id))
        if not record:
            return await ctx.send("No tracked invite record exists for that member.")
        inviter = f"<@{record['inviter_id']}>" if record.get("inviter_id") else "Unknown"
        embed = discord.Embed(title=f"Invited By — {member}", colour=COLOUR)
        embed.add_field(name="Inviter", value=inviter)
        embed.add_field(name="Source", value=record["source"].title())
        embed.add_field(name="Confidence", value=record["confidence"].title())
        embed.add_field(name="Status", value=record["review_status"].replace("_", " ").title(), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="inviteleaderboard", aliases=["invitelb"])
    @commands.guild_only()
    async def inviteleaderboard(self, ctx: CustomContext, page: int = 1):
        """Show the server invite leaderboard ranked by net invites."""
        data = self._guild_data(ctx.guild.id)
        ids = {int(r["inviter_id"]) for r in data["members"].values() if r.get("inviter_id")}
        ids.update(int(uid) for uid in data["adjustments"])
        rows = [(uid, self._stats(ctx.guild.id, uid)) for uid in ids]
        rows.sort(key=lambda row: (-row[1]["net"], -row[1]["valid"], row[0]))
        page = max(1, page)
        start = (page - 1) * 10
        shown = rows[start:start + 10]
        embed = discord.Embed(title=f"🏆 Invite Leaderboard — {ctx.guild.name}", colour=COLOUR)
        embed.description = "\n".join(
            f"`{start + i}.` <@{uid}> — **{stats['net']} net** ({stats['valid']} valid)"
            for i, (uid, stats) in enumerate(shown, 1)
        ) or "No invite data yet."
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="invitedlist")
    @commands.guild_only()
    async def invitedlist(self, ctx: CustomContext, member: discord.Member = None, page: int = 1):
        """List members attributed to an inviter."""
        member = member or ctx.author
        records = [r for r in self._guild_data(ctx.guild.id)["members"].values() if r.get("inviter_id") == member.id]
        records.sort(key=lambda r: r["joined_at"], reverse=True)
        start = (max(1, page) - 1) * 10
        lines = []
        for record in records[start:start + 10]:
            status = "Left" if record.get("left_at") else (
                "Valid" if record.get("counted") else record["review_status"].replace("_", " ").title()
            )
            lines.append(f"<@{record['member_id']}> — **{status}**")
        embed = discord.Embed(title=f"Invited Members — {member}", description="\n".join(lines) or "No records.", colour=COLOUR)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="invitesetup")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def invitesetup(self, ctx: CustomContext, channel: discord.TextChannel = None):
        """Create a current-use baseline and enable invite tracking."""
        me = ctx.guild.me
        if not me.guild_permissions.manage_guild:
            return await ctx.send("❌ I need **Manage Server** to read invite usage.")
        try:
            await self._resync_guild(ctx.guild, ctx.author.id)
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.send("❌ I could not fetch this server's invites.")
        data = self._guild_data(ctx.guild.id)
        data["settings"]["enabled"] = True
        if channel:
            data["settings"]["log_channel_id"] = channel.id
        append_audit(data, "setup", ctx.author.id)
        self.store.mark_dirty()
        await self.store.flush()
        await ctx.send("✅ Invite tracking enabled. Current invite uses are the baseline and were not counted.")

    @commands.hybrid_group(name="invitesettings", invoke_without_command=True)
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def invitesettings(self, ctx: CustomContext):
        """View or change invite tracker settings."""
        data = self._guild_data(ctx.guild.id)
        s = data["settings"]
        embed = discord.Embed(title="⚙️ Invite Tracker Settings", colour=COLOUR)
        embed.add_field(name="Tracking", value="On" if s["enabled"] else "Off")
        embed.add_field(name="Alt Age", value=f"{s['alt_age_days']} days")
        embed.add_field(name="Log Channel", value=f"<#{s['log_channel_id']}>" if s["log_channel_id"] else "Off")
        embed.add_field(name="DM Alerts", value="On" if s["dm_alerts_enabled"] else "Off")
        embed.add_field(name="Join Messages", value="On" if s["join_messages_enabled"] else "Off")
        embed.add_field(name="Join Channel", value=f"<#{s['join_channel_id']}>" if s["join_channel_id"] else "Fallback/Off")
        embed.add_field(name="Baseline", value=f"<t:{data['baseline_started_at']}:R>" if data["baseline_started_at"] else "Not created", inline=False)
        await ctx.send(embed=embed)

    async def _set_and_reply(self, ctx, key, value, label):
        update_setting(self._guild_data(ctx.guild.id)["settings"], key, value)
        append_audit(self._guild_data(ctx.guild.id), "setting_changed", ctx.author.id, details={"setting": key, "value": value})
        self.store.mark_dirty()
        await self.store.flush()
        await ctx.send(f"✅ {label} updated.")

    @invitesettings.command(name="tracking")
    async def settings_tracking(self, ctx: CustomContext, state: str):
        try:
            enabled = _on_off(state)
        except ValueError as exc:
            return await ctx.send(f"❌ {exc}")
        if enabled:
            try:
                await self._resync_guild(ctx.guild, ctx.author.id)
            except (discord.Forbidden, discord.HTTPException):
                return await ctx.send("❌ Could not fetch invites; tracking was not enabled.")
        await self._set_and_reply(ctx, "enabled", enabled, "Tracking")

    @invitesettings.command(name="altage")
    async def settings_altage(self, ctx: CustomContext, days: int):
        try:
            await self._set_and_reply(ctx, "alt_age_days", days, "Alt age")
        except ValueError as exc:
            await ctx.send(f"❌ {exc}")

    @invitesettings.command(name="logchannel")
    async def settings_logchannel(self, ctx: CustomContext, channel: discord.TextChannel = None):
        await self._set_and_reply(ctx, "log_channel_id", channel.id if channel else None, "Log channel")

    @invitesettings.command(name="dmalerts")
    async def settings_dmalerts(self, ctx: CustomContext, state: str):
        try:
            enabled = _on_off(state)
        except ValueError as exc:
            return await ctx.send(f"❌ {exc}")
        await self._set_and_reply(ctx, "dm_alerts_enabled", enabled, "DM alerts")

    @invitesettings.command(name="joinmessage")
    async def settings_joinmessage(self, ctx: CustomContext, state: str):
        try:
            enabled = _on_off(state)
        except ValueError as exc:
            return await ctx.send(f"❌ {exc}")
        await self._set_and_reply(ctx, "join_messages_enabled", enabled, "Join messages")

    @invitesettings.command(name="joinchannel")
    async def settings_joinchannel(self, ctx: CustomContext, channel: discord.TextChannel = None):
        await self._set_and_reply(ctx, "join_channel_id", channel.id if channel else None, "Join channel")

    @commands.hybrid_command(name="invitealts")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def invitealts(self, ctx: CustomContext, member: discord.Member = None, page: int = 1):
        """List suspected-alt invitations, optionally filtered by inviter."""
        records = [
            r for r in self._guild_data(ctx.guild.id)["members"].values()
            if r.get("classification") == "suspected_alt"
            and r.get("review_status") in {"pending", "denied"}
            and (member is None or r.get("inviter_id") == member.id)
        ]
        records.sort(key=lambda r: r["joined_at"], reverse=True)
        start = (max(1, page) - 1) * 10
        lines = [
            f"<@{r['member_id']}> ← <@{r['inviter_id']}> • {r['account_age_seconds']/86400:.1f}d • **{r['review_status']}**"
            for r in records[start:start + 10]
        ]
        await ctx.send(embed=discord.Embed(title="⚠️ Suspected Alt Invites", description="\n".join(lines) or "No matching records.", colour=discord.Colour.orange()))

    @commands.hybrid_group(name="invitealt", invoke_without_command=True)
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def invitealt(self, ctx: CustomContext):
        """Review suspected-alt records."""
        await ctx.send_help(ctx.command)

    @invitealt.command(name="pending")
    async def alt_pending(self, ctx: CustomContext, page: int = 1):
        await self.invitealts.callback(self, ctx, None, page)

    async def _review(self, ctx, member, approve, reason):
        data = self._guild_data(ctx.guild.id)
        record = data["members"].get(str(member.id))
        if not record or record.get("review_status") not in {"pending", "denied", "approved"}:
            return await ctx.send("❌ That member has no suspected-alt review record.")
        try:
            review_alt(record, approve, ctx.author.id, reason, int(discord.utils.utcnow().timestamp()))
        except ValueError as exc:
            return await ctx.send(f"❌ {exc}")
        append_audit(data, "alt_approved" if approve else "alt_denied", ctx.author.id, member.id, {"reason": reason or ""})
        self.store.mark_dirty()
        await self.store.flush()
        action = "approved and counted" if approve else "denied and excluded"
        embed = discord.Embed(title=f"Alt Review — {member}", description=f"✅ Record {action}.", colour=discord.Colour.green() if approve else discord.Colour.red())
        await ctx.send(embed=embed)
        await self._send_log(ctx.guild, embed)

    @invitealt.command(name="approve")
    async def alt_approve(self, ctx: CustomContext, member: discord.Member, *, reason: str = None):
        await self._review(ctx, member, True, reason)

    @invitealt.command(name="deny")
    async def alt_deny(self, ctx: CustomContext, member: discord.Member, *, reason: str = None):
        await self._review(ctx, member, False, reason)

    async def _adjust(self, ctx, member, amount, reason, sign):
        if not 1 <= amount <= 1_000_000:
            return await ctx.send("❌ Amount must be between 1 and 1,000,000.")
        data = self._guild_data(ctx.guild.id)
        key = str(member.id)
        data["adjustments"][key] = int(data["adjustments"].get(key, 0)) + amount * sign
        append_audit(data, "adjustment", ctx.author.id, member.id, {"amount": amount * sign, "reason": reason or ""})
        self.store.mark_dirty()
        await self.store.flush()
        await ctx.send(embed=self._stats_embed(ctx.guild, member))

    @commands.hybrid_command(name="inviteadd")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def inviteadd(self, ctx: CustomContext, member: discord.Member, amount: int, *, reason: str = None):
        """Add bonus invites to a member."""
        await self._adjust(ctx, member, amount, reason, 1)

    @commands.hybrid_command(name="inviteremove")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def inviteremove(self, ctx: CustomContext, member: discord.Member, amount: int, *, reason: str = None):
        """Remove bonus invites from a member."""
        await self._adjust(ctx, member, amount, reason, -1)

    @commands.hybrid_command(name="invitehistory")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def invitehistory(self, ctx: CustomContext, member: discord.Member = None, page: int = 1):
        """View invite tracker audit history."""
        events = self._guild_data(ctx.guild.id)["audit_log"]
        if member:
            events = [e for e in events if e.get("target_id") == member.id or e.get("actor_id") == member.id]
        events = list(reversed(events))
        start = (max(1, page) - 1) * 10
        lines = [f"<t:{e['timestamp']}:R> **{e['event'].replace('_', ' ').title()}** • target `{e.get('target_id') or '-'}`" for e in events[start:start + 10]]
        await ctx.send(embed=discord.Embed(title="Invite Tracker History", description="\n".join(lines) or "No matching history.", colour=COLOUR))

    @commands.hybrid_command(name="inviteresync")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def inviteresync(self, ctx: CustomContext):
        """Refresh invite snapshots without importing old uses."""
        try:
            await self._resync_guild(ctx.guild, ctx.author.id)
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.send("❌ Could not fetch invites.")
        await ctx.send("✅ Invite snapshot refreshed. No historical uses were imported.")

    @commands.hybrid_group(name="invitereset", invoke_without_command=True)
    @commands.has_guild_permissions(administrator=True)
    @commands.guild_only()
    async def invitereset(self, ctx: CustomContext):
        """Reset member or server invite tracker data."""
        await ctx.send_help(ctx.command)

    @invitereset.command(name="member")
    async def reset_member_command(self, ctx: CustomContext, member: discord.Member):
        data = self._guild_data(ctx.guild.id)
        if not reset_member(data, member.id, ctx.author.id):
            return await ctx.send("No invite record exists for that member.")
        self.store.mark_dirty()
        await self.store.flush()
        await ctx.send(f"✅ Reset invite relationship for {member.mention}.")

    @invitereset.command(name="server")
    async def reset_server_command(self, ctx: CustomContext):
        view = ResetConfirmView(self, ctx.guild.id, ctx.author.id)
        await ctx.send("⚠️ This permanently deletes all invite tracker data for this server.", view=view)

    def help_embed(self, prefix: str = "!", guild=None) -> discord.Embed:
        embed = discord.Embed(
            title="🔗 Invite Tracker",
            description="Tracks invites from setup onward. **Net = Valid + Bonus - Left.** Suspected alts are recorded but excluded until approved.",
            colour=COLOUR,
        )
        embed.add_field(name="Public", value=f"`{prefix}invites [member]`\n`{prefix}invitedby [member]`\n`{prefix}inviteleaderboard [page]`\n`{prefix}invitedlist [member] [page]`", inline=False)
        embed.add_field(name="Setup & Configuration", value=f"`{prefix}invitesetup [channel]`\n`{prefix}invitesettings`\nSubcommands: `tracking`, `altage`, `logchannel`, `dmalerts`, `joinmessage`, `joinchannel`", inline=False)
        embed.add_field(name="Alt Review", value=f"`{prefix}invitealts [inviter] [page]`\n`{prefix}invitealt pending`\n`{prefix}invitealt approve <member> [reason]`\n`{prefix}invitealt deny <member> [reason]`", inline=False)
        embed.add_field(name="Administration", value=f"`{prefix}inviteadd` • `{prefix}inviteremove` • `{prefix}invitehistory` • `{prefix}inviteresync`\n`{prefix}invitereset member|server`", inline=False)
        embed.set_footer(text="Manage Server: configuration/review • Administrator: resets • Default alt age: 14 days")
        return embed


async def setup(bot):
    await bot.add_cog(InviteTracker(bot))
