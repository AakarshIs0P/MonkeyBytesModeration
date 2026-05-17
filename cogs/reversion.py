"""
cogs/reversion.py  —  Paladin Reversion System
================================================

Automatically reverses guild actions when Paladin's antinuke fires.

HOW IT WORKS
------------
  When Paladin strips a user's roles (via _an_punish), it immediately calls
  reversion_cog.on_paladin_action_reversed(guild, actor, action, obj).
  This is a direct synchronous callback — no polling, no races.

  The reversion cog stores pre-event snapshots and uses them to undo the
  specific action that triggered the threshold.

EVENTS COVERED
--------------
  * channel_create  -> delete the newly-created channel
  * channel_delete  -> recreate the channel from snapshot
  * role_create     -> delete the newly-created role
  * role_delete     -> recreate the role from snapshot
  * ban             -> unban + re-invite DM
  * kick            -> DM the kicked user a re-invite
  * guild_update    -> restore name, verification level, content filter
  * bot_add         -> kick the unauthorized bot
  * webhook         -> delete newly-created webhooks

BYPASS
------
  Respects OWNERS / guild-owner / whitelist hierarchy from paladin.py.
  If the actor is protected, Paladin never calls us.
"""

import asyncio
import datetime
import logging
import time

import discord
from discord.ext import commands

from utils.data import DiscordBot

log = logging.getLogger("bot.reversion")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ── Embed helpers ─────────────────────────────────────────────────────────────

COL_SUCCESS = discord.Colour.from_str("#1E8449")   # dark green
COL_FAIL    = discord.Colour.red()
COL_PARTIAL = discord.Colour.orange()


def _base_embed(title: str, colour: discord.Colour, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, colour=colour, timestamp=_now())
    if description:
        e.description = description
    e.set_footer(text="Paladin Reversion  |  MonkeyBytes")
    return e


def _reversed_embed(action: str, actor: discord.Member, detail: str) -> discord.Embed:
    e = _base_embed("⚡  Action Reversed", COL_SUCCESS, detail)
    e.add_field(name="Action", value=f"`{action}`",                     inline=True)
    e.add_field(name="Actor",  value=f"{actor.mention} (`{actor.id}`)", inline=True)
    e.set_author(name=str(actor), icon_url=actor.display_avatar.url)
    e.set_thumbnail(url=actor.display_avatar.url)
    return e


def _failed_embed(action: str, actor: discord.Member, reason: str) -> discord.Embed:
    e = _base_embed(
        "❌  Reversion Failed", COL_FAIL,
        f"Tried to reverse `{action}` but hit an error. Manual action may be needed."
    )
    e.add_field(name="Actor",  value=f"{actor.mention} (`{actor.id}`)", inline=True)
    e.add_field(name="Reason", value=f"```{reason[:200]}```",           inline=False)
    return e


def _partial_embed(action: str, actor: discord.Member, detail: str) -> discord.Embed:
    e = _base_embed("⚠️  Partial Reversion", COL_PARTIAL, detail)
    e.add_field(name="Action", value=f"`{action}`",                     inline=True)
    e.add_field(name="Actor",  value=f"{actor.mention} (`{actor.id}`)", inline=True)
    return e


# ── Snapshot helpers ──────────────────────────────────────────────────────────

def _snap_channel(channel: discord.abc.GuildChannel) -> dict:
    snap = {
        "id":          channel.id,
        "name":        channel.name,
        "type":        channel.type,
        "position":    channel.position,
        "category_id": channel.category_id,
        "overwrites": {
            str(target.id): {
                "type":  "role" if isinstance(target, discord.Role) else "member",
                "allow": overwrite.pair()[0].value,
                "deny":  overwrite.pair()[1].value,
            }
            for target, overwrite in channel.overwrites.items()
        },
    }
    if isinstance(channel, discord.TextChannel):
        snap.update({"topic": channel.topic, "nsfw": channel.nsfw, "slowmode": channel.slowmode_delay})
    elif isinstance(channel, discord.VoiceChannel):
        snap.update({"bitrate": channel.bitrate, "user_limit": channel.user_limit})
    return snap


def _snap_role(role: discord.Role) -> dict:
    return {
        "id":          role.id,
        "name":        role.name,
        "colour":      role.colour.value,
        "hoist":       role.hoist,
        "mentionable": role.mentionable,
        "permissions": role.permissions.value,
        "position":    role.position,
    }


def _snap_guild(guild: discord.Guild) -> dict:
    return {
        "name":               guild.name,
        "verification_level": guild.verification_level,
        "explicit_content_filter": guild.explicit_content_filter,
        "afk_timeout":        guild.afk_timeout,
        "afk_channel_id":     guild.afk_channel.id if guild.afk_channel else None,
    }


# ═════════════════════════════════════════════════════════════════════════════
#   COG
# ═════════════════════════════════════════════════════════════════════════════

class Reversion(commands.Cog, name="Reversion"):
    """
    Paladin Reversion System.
    Called directly by Paladin when an actor trips the antinuke threshold.
    Automatically reverses the specific action that triggered the punishment.
    """

    def __init__(self, bot: DiscordBot):
        self.bot = bot

        # Snapshot stores — (guild_id, object_id) -> snap dict
        self._channel_snaps: dict[tuple, dict] = {}
        self._role_snaps:    dict[tuple, dict] = {}
        self._guild_snaps:   dict[int, dict]   = {}

        # Cooldown guard — prevent double-reversions
        # (guild_id, action, obj_id) -> monotonic timestamp
        self._cooldowns: dict[tuple, float] = {}

        # Fix 2: Strong-reference set for fire-and-forget tasks.
        # asyncio.create_task() only holds a *weak* reference internally; under heavy
        # event-loop load the garbage collector can silently destroy the task object
        # mid-execution.  Adding each task to this set keeps it alive until it
        # finishes, at which point the done-callback removes it.
        self._background_tasks: set[asyncio.Task] = set()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _on_cooldown(self, guild_id: int, action: str, obj_id: int) -> bool:
        key  = (guild_id, action, obj_id)
        last = self._cooldowns.get(key, 0.0)
        now  = time.monotonic()
        if (now - last) < 5.0:
            return True
        self._cooldowns[key] = now
        return False

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        p = self.bot.cogs.get("Paladin")
        if p:
            await p._log(guild, embed)
            return
        lc = self.bot.cogs.get("Logging")
        if lc:
            await lc._log(guild.id, embed)

    async def _make_invite(self, guild: discord.Guild) -> str | None:
        try:
            ch = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel)
                and c.permissions_for(guild.me).create_instant_invite,
                guild.text_channels,
            )
            if ch:
                inv = await ch.create_invite(max_uses=1, max_age=86400, reason="Paladin reversion re-invite")
                return inv.url
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    # ── Pre-event snapshot listeners ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        self._channel_snaps[(channel.guild.id, channel.id)] = _snap_channel(channel)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        # Discord gives us the channel object even after deletion
        self._channel_snaps[(channel.guild.id, channel.id)] = _snap_channel(channel)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        self._role_snaps[(role.guild.id, role.id)] = _snap_role(role)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        self._role_snaps[(role.guild.id, role.id)] = _snap_role(role)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        self._guild_snaps[before.id] = _snap_guild(before)

    # ── Main callback from Paladin ────────────────────────────────────────────

    async def on_paladin_action_reversed(
        self,
        guild:  discord.Guild,
        actor:  discord.Member,
        action: str,
        obj,
    ):
        """
        Called by Paladin._an_punish() synchronously after stripping the actor.
        action: the Paladin action key (e.g. "ban", "channel_delete", ...)
        obj:    the relevant Discord object; may be None for some actions.
        """
        log.info(f"[reversion] callback: action={action} actor={actor} guild={guild.id}")
        task = asyncio.create_task(self._do_revert(guild, actor, action, obj))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _do_revert(self, guild: discord.Guild, actor: discord.Member, action: str, obj):
        obj_id = getattr(obj, "id", 0)
        if self._on_cooldown(guild.id, action, obj_id):
            log.debug(f"[reversion] cooldown: ({guild.id}, {action}, {obj_id})")
            return

        handlers = {
            "channel_create": self._revert_channel_create,
            "channel_delete": self._revert_channel_delete,
            "role_create":    self._revert_role_create,
            "role_delete":    self._revert_role_delete,
            "ban":            self._revert_ban,
            "kick":           self._revert_kick,
            "guild_update":   self._revert_guild_update,
            "bot_add":        self._revert_bot_add,
            "webhook":        self._revert_webhook,
        }
        handler = handlers.get(action)
        if handler is None:
            log.debug(f"[reversion] no handler for: {action}")
            return

        try:
            await handler(guild, actor, obj)
        except Exception as e:
            log.exception(f"[reversion] unhandled error in {action}: {e}")
            try:
                await self._log(guild, _failed_embed(action, actor, str(e)))
            except Exception:
                pass

    # ── Reversion handlers ────────────────────────────────────────────────────

    async def _revert_channel_create(self, guild, actor, channel):
        if channel is None:
            return
        try:
            await channel.delete(reason="[Paladin Reversion] Antinuke — rogue channel_create")
            embed = _reversed_embed("channel_create", actor, f"Rogue channel `#{channel.name}` (`{channel.id}`) deleted.")
            embed.add_field(name="Channel", value=f"`#{channel.name}`", inline=True)
            embed.add_field(name="Type",    value=f"`{channel.type}`",  inline=True)
            await self._log(guild, embed)
        except discord.Forbidden:
            await self._log(guild, _failed_embed("channel_create", actor, "Missing permissions to delete channel."))
        except discord.HTTPException as e:
            await self._log(guild, _failed_embed("channel_create", actor, str(e)))

    async def _revert_channel_delete(self, guild, actor, channel):
        if channel is None:
            return
        snap = self._channel_snaps.get((guild.id, channel.id))
        if not snap:
            await self._log(guild, _partial_embed("channel_delete", actor,
                f"No snapshot for `#{channel.name}` — cannot recreate. Channel is permanently lost."))
            return
        try:
            category   = guild.get_channel(snap.get("category_id")) if snap.get("category_id") else None
            overwrites = {}
            for tid_s, ow in snap.get("overwrites", {}).items():
                tid = int(tid_s)
                if ow["type"] == "role":
                    target = guild.get_role(tid)
                else:
                    # Fix 5: guild.get_member() only searches the in-memory cache and
                    # returns None for offline or uncached members, silently dropping
                    # their channel overwrites.  Fall back to fetch_member() to ensure
                    # the permission is restored even when the user is offline.
                    target = guild.get_member(tid)
                    if target is None:
                        try:
                            target = await guild.fetch_member(tid)
                        except (discord.NotFound, discord.HTTPException):
                            pass  # member left the guild; skip their overwrite
                if target:
                    overwrites[target] = discord.PermissionOverwrite.from_pair(
                        discord.Permissions(ow["allow"]), discord.Permissions(ow["deny"])
                    )

            ch_type = snap["type"]
            if ch_type == discord.ChannelType.text:
                new_ch = await guild.create_text_channel(
                    name=snap["name"], category=category, position=snap["position"],
                    topic=snap.get("topic"), nsfw=snap.get("nsfw", False),
                    slowmode_delay=snap.get("slowmode", 0), overwrites=overwrites,
                    reason="[Paladin Reversion] Antinuke — channel_delete restore",
                )
            elif ch_type == discord.ChannelType.voice:
                new_ch = await guild.create_voice_channel(
                    name=snap["name"], category=category, position=snap["position"],
                    bitrate=snap.get("bitrate", 64000), user_limit=snap.get("user_limit", 0),
                    overwrites=overwrites, reason="[Paladin Reversion] Antinuke — channel_delete restore",
                )
            elif ch_type == discord.ChannelType.category:
                new_ch = await guild.create_category(
                    name=snap["name"], position=snap["position"], overwrites=overwrites,
                    reason="[Paladin Reversion] Antinuke — channel_delete restore",
                )
            else:
                new_ch = await guild.create_text_channel(
                    name=snap["name"], category=category,
                    reason="[Paladin Reversion] Antinuke — channel_delete restore",
                )

            embed = _reversed_embed("channel_delete", actor, f"Restored deleted channel `#{snap['name']}` as {new_ch.mention}.")
            embed.add_field(name="Original ID", value=f"`{snap['id']}`",    inline=True)
            embed.add_field(name="New Channel",  value=new_ch.mention,      inline=True)
            embed.add_field(name="Type",         value=f"`{snap['type']}`", inline=True)
            await self._log(guild, embed)
        except discord.Forbidden:
            await self._log(guild, _failed_embed("channel_delete", actor, "Missing permissions to recreate channel."))
        except discord.HTTPException as e:
            await self._log(guild, _failed_embed("channel_delete", actor, str(e)))

    async def _revert_role_create(self, guild, actor, role):
        if role is None:
            return
        try:
            await role.delete(reason="[Paladin Reversion] Antinuke — rogue role_create")
            embed = _reversed_embed("role_create", actor, f"Rogue role `@{role.name}` (`{role.id}`) deleted.")
            embed.add_field(name="Role", value=f"`@{role.name}`", inline=True)
            await self._log(guild, embed)
        except discord.Forbidden:
            await self._log(guild, _failed_embed("role_create", actor, "Missing permissions to delete role."))
        except discord.HTTPException as e:
            await self._log(guild, _failed_embed("role_create", actor, str(e)))

    async def _revert_role_delete(self, guild, actor, role):
        if role is None:
            return
        snap = self._role_snaps.get((guild.id, role.id))
        if not snap:
            await self._log(guild, _partial_embed("role_delete", actor,
                f"No snapshot for `@{role.name}` — cannot recreate."))
            return
        try:
            new_role = await guild.create_role(
                name=snap["name"], colour=discord.Colour(snap["colour"]),
                hoist=snap["hoist"], mentionable=snap["mentionable"],
                permissions=discord.Permissions(snap["permissions"]),
                reason="[Paladin Reversion] Antinuke — role_delete restore",
            )
            try:
                await new_role.edit(position=snap["position"])
            except (discord.Forbidden, discord.HTTPException):
                pass

            embed = _reversed_embed("role_delete", actor, f"Restored deleted role `@{snap['name']}` as {new_role.mention}.")
            embed.add_field(name="Original ID",  value=f"`{snap['id']}`",          inline=True)
            embed.add_field(name="New Role",      value=new_role.mention,           inline=True)
            embed.add_field(name="Permissions",   value=f"`{snap['permissions']}`", inline=True)
            await self._log(guild, embed)
        except discord.Forbidden:
            await self._log(guild, _failed_embed("role_delete", actor, "Missing permissions to recreate role."))
        except discord.HTTPException as e:
            await self._log(guild, _failed_embed("role_delete", actor, str(e)))

    async def _revert_ban(self, guild, actor, user):
        if user is None:
            return
        await asyncio.sleep(1.0)   # ensure ban is committed before unban
        try:
            await guild.unban(user, reason="[Paladin Reversion] Antinuke — unauthorized ban reversed")
            invite_url = await self._make_invite(guild)
            try:
                dm = _base_embed(
                    "🛡️  Your Ban Was Reversed", discord.Colour.from_str("#1E8449"),
                    f"Your ban from **{guild.name}** was automatically reversed.\n"
                    f"The moderator has been dealt with.\n\n"
                    + (f"**Re-join:** {invite_url}" if invite_url else "Contact a server admin for an invite."),
                )
                await user.send(embed=dm)
            except (discord.Forbidden, discord.HTTPException):
                pass

            embed = _reversed_embed("ban", actor,
                f"Unauthorized ban of `{user}` (`{user.id}`) reversed.\n"
                + (f"Re-invite sent." if invite_url else "No invite generated."))
            embed.add_field(name="Unbanned", value=f"`{user}` — `{user.id}`", inline=True)
            await self._log(guild, embed)
        except discord.Forbidden:
            await self._log(guild, _failed_embed("ban", actor, "Missing permissions to unban."))
        except discord.HTTPException as e:
            await self._log(guild, _failed_embed("ban", actor, str(e)))

    async def _revert_kick(self, guild, actor, member):
        if member is None:
            return
        invite_url = await self._make_invite(guild)
        try:
            dm = _base_embed(
                "🛡️  Your Kick Was Reversed", discord.Colour.from_str("#1E8449"),
                f"You were kicked from **{guild.name}**, but this was automatically reversed.\n\n"
                + (f"**Re-join:** {invite_url}" if invite_url else "Contact a server admin for an invite."),
            )
            await member.send(embed=dm)
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = _reversed_embed("kick", actor,
            f"Unauthorized kick of `{member}` (`{member.id}`) reversed.")
        embed.add_field(name="Kicked User", value=f"`{member}` — `{member.id}`", inline=True)
        await self._log(guild, embed)

    async def _revert_guild_update(self, guild, actor, _obj):
        snap = self._guild_snaps.get(guild.id)
        if not snap:
            await self._log(guild, _partial_embed("guild_update", actor, "No guild snapshot — cannot restore settings."))
            return
        try:
            kwargs  = {}
            changes = []
            if guild.name != snap["name"]:
                kwargs["name"] = snap["name"]
                changes.append(f"name: `{guild.name}` → `{snap['name']}`")
            if guild.verification_level != snap["verification_level"]:
                kwargs["verification_level"] = snap["verification_level"]
                changes.append(f"verification: `{guild.verification_level}` → `{snap['verification_level']}`")
            if guild.explicit_content_filter != snap["explicit_content_filter"]:
                kwargs["explicit_content_filter"] = snap["explicit_content_filter"]
                changes.append(f"content filter changed")

            if kwargs:
                await guild.edit(**kwargs, reason="[Paladin Reversion] Antinuke — guild_update restore")

            embed = _reversed_embed("guild_update", actor, "Guild settings restored to pre-attack state.")
            embed.add_field(name="Changes Reversed",
                value="\n".join(changes) if changes else "No restorable changes.", inline=False)
            await self._log(guild, embed)
        except discord.Forbidden:
            await self._log(guild, _failed_embed("guild_update", actor, "Missing permissions to edit guild."))
        except discord.HTTPException as e:
            await self._log(guild, _failed_embed("guild_update", actor, str(e)))

    async def _revert_bot_add(self, guild, actor, bot_member):
        if bot_member is None:
            return
        try:
            await bot_member.kick(reason="[Paladin Reversion] Antinuke — unauthorized bot_add")
            embed = _reversed_embed("bot_add", actor, f"Unauthorized bot `{bot_member}` (`{bot_member.id}`) kicked.")
            embed.add_field(name="Bot Removed", value=f"`{bot_member}` — `{bot_member.id}`", inline=True)
            await self._log(guild, embed)
        except discord.Forbidden:
            await self._log(guild, _failed_embed("bot_add", actor, "Missing permissions to kick bot."))
        except discord.HTTPException as e:
            await self._log(guild, _failed_embed("bot_add", actor, str(e)))

    async def _revert_webhook(self, guild, actor, channel):
        if channel is None:
            return
        # Fix 8: CategoryChannel (and StageChannel in some versions) does not
        # have a .webhooks() method.  Calling it raises AttributeError and
        # crashes the entire reversion chain.  Only proceed for channel types
        # that actually support webhooks.
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel,
                                    discord.VoiceChannel)):
            log.debug(
                f"[reversion] _revert_webhook: skipping unsupported channel type "
                f"{type(channel).__name__} ({channel.id})"
            )
            return
        try:
            webhooks = await channel.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            return

        deleted = []
        for wh in webhooks:
            if wh.created_at and (discord.utils.utcnow() - wh.created_at).total_seconds() < 15:
                try:
                    await wh.delete(reason="[Paladin Reversion] Antinuke — webhook_create")
                    deleted.append(wh.name)
                except (discord.Forbidden, discord.HTTPException):
                    pass

        if deleted:
            embed = _reversed_embed("webhook", actor, f"Unauthorized webhook(s) deleted from `#{channel.name}`.")
            embed.add_field(name="Webhooks Deleted", value=", ".join(f"`{n}`" for n in deleted), inline=False)
            await self._log(guild, embed)

    @commands.Cog.listener()
    async def on_ready(self):
        log.info(f"[reversion] Paladin Reversion System online across {len(self.bot.guilds)} guild(s).")


async def setup(bot: DiscordBot):
    await bot.add_cog(Reversion(bot))
