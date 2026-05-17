"""
cogs/paladin.py  --  Paladin Protection System
===============================================

Two independent protection layers, both configurable per guild:

  LAYER 1 - ANTINUKE  (privileged users / moderators)
    Watches: ban, kick, channel_delete, channel_create, role_delete,
             role_create, guild_update, bot_add, webhook_create.
    Per-action thresholds inside a rolling window.
    
    For ban/kick via !ban / !kick commands:
      • mod.py calls pre_check_mod_action() the moment the command is invoked.
        If the actor is already at or would breach threshold → block immediately,
        strip roles, show a "blocked by Paladin" embed — confirmation embed is
        never shown.
      • If pre-check passes, show the confirmation embed.
      • When Confirm is clicked, mod.py calls check_mod_action() as a race-
        condition safety re-check.  Neither call pre-records the action; the
        audit-log listener is the sole recorder, eliminating double-counting.
    
    For other actions (auto-triggered):
      • Punishment: ALL roles stripped immediately when threshold hit.
    
    Special case bot_add: the added bot is BANNED immediately and the 
    inviter's roles are stripped. A single embed reports both actions and 
    explains if the inviter could not be punished (whitelisted / owner / 
    hardcoded owner).
    
    Special case rogue_bot: If a bot triggers a threshold, it is BANNED,
    its original inviter is tracked down via audit logs, and the inviter's
    roles are stripped.

  LAYER 2 - AUTOMOD  (regular non-privileged members)
    Watches: message spam (fast messages in a short window).
    Progressive: Strike 1 -> Warn DM  |  2 -> Timeout  |  3 -> Kick  |  4+ -> Ban
    Strikes persist across restarts, expire after warn_expire (default 6 h).

  BYPASS HIERARCHY  (both layers)
    1. Hardcoded OWNERS
    2. Guild server owner
    3. Per-guild whitelist  (!whitelist / /whitelist)

  COMMANDS
    !paladinstart
    !paladinset  [threshold <action> <n> | decay <dur> | window <dur>]
    !paladinreset
    !automodstart
    !automodset  [spam_count <n> | spam_window <s> | timeout <dur> | warn_expire <dur>]
    !automodclear @user
    !whitelist @user
    !whitelistshow
    !alertnuke @user   -- toggle DM alerts for antinuke/automod events
    !paladinalert @user -- toggle DM alerts with confirmation DM
    !alertnukelist     -- show current alert subscribers
"""

import asyncio
import datetime
import json
import logging
import os
import re
import secrets
import time
import copy

from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.data import DiscordBot
from utils.permissions import OWNERS

log = logging.getLogger("bot.paladin")

# ---- File paths --------------------------------------------------------------
DATA_FILE         = "data/paladin.json"
WHITELIST_FILE    = "data/paladin_whitelist.json"
AUTOMOD_DATA_FILE = "data/paladin_automod.json"
ALERTNUKE_FILE    = "data/paladin_alertnuke.json"
KEYS_FILE         = "data/paladin_keys.json"

# ---- Defaults ----------------------------------------------------------------
DEFAULT_ANTINUKE_THRESHOLDS = {
    "ban":            2,
    "kick":           2,
    "channel_delete": 2,
    "channel_create": 4,
    "role_delete":    2,
    "role_create":    4,
    "guild_update":   1,
    "bot_add":        1,
    "webhook":        2,
    "emoji_delete":   4,
    "emoji_create":   4,
    "sticker_delete": 4,
    "sticker_create": 4,
    "member_prune":   1,
}
DEFAULT_DECAY_S   = 1800    # 30 min
DEFAULT_WINDOW_S  = 60      # 60 sec
DEFAULT_SPAM_COUNT  = 6
DEFAULT_SPAM_WINDOW = 5     # seconds
DEFAULT_TIMEOUT_S   = 600   # 10 min
DEFAULT_WARN_EXPIRE = 21600  # 6 hours

# ---- Design system -----------------------------------------------------------
COL_BRAND    = discord.Colour.from_str("#F1C40F")   # gold
COL_CRITICAL = discord.Colour.from_str("#922B21")   # deep crimson
COL_DANGER   = discord.Colour.from_str("#E74C3C")   # red
COL_WARNING  = discord.Colour.from_str("#E67E22")   # amber
COL_SUCCESS  = discord.Colour.from_str("#1E8449")   # dark green
COL_MUTED    = discord.Colour.from_str("#566573")   # slate
COL_ERROR    = discord.Colour.from_str("#C0392B")   # dark red

# Consistent icon set
ICO_SHIELD   = "🛡️"
ICO_ANTINUKE = "🔴"
ICO_AUTOMOD  = "🟠"
ICO_WL       = "📋"
ICO_SUCCESS  = "✅"
ICO_ERROR    = "❌"
ICO_WARN     = "⚠️"
ICO_INFO     = "ℹ️"
ICO_STRIP    = "✂️"
ICO_KEY      = "🔑"

# Action metadata: (display label, icon)
ACTION_META = {
    "ban":            ("Ban",            "🔨"),
    "kick":           ("Kick",           "👢"),
    "channel_delete": ("Channel Delete", "🗑️"),
    "channel_create": ("Channel Create", "📺"),
    "role_delete":    ("Role Delete",    "🗑️"),
    "role_create":    ("Role Create",    "🎭"),
    "guild_update":   ("Server Edit",    "⚙️"),
    "bot_add":        ("Bot Add",        "🤖"),
    "webhook":        ("Webhook Create", "🔗"),
    "emoji_delete":   ("Emoji Delete",   "🗑️"),
    "emoji_create":   ("Emoji Create",   "😀"),
    "sticker_delete": ("Sticker Delete", "🗑️"),
    "sticker_create": ("Sticker Create", "💥"),
    "member_prune":   ("Member Prune",   "👢"),
}

def _action_label(action: str) -> str:
    meta = ACTION_META.get(action)
    return f"{meta[1]} {meta[0]}" if meta else action

# ---- Duration helpers --------------------------------------------------------

def _parse_duration(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
    if not m or not any(m.groups()):
        return None
    total = (
        int(m.group(1) or 0) * 86400 +
        int(m.group(2) or 0) * 3600 +
        int(m.group(3) or 0) * 60 +
        int(m.group(4) or 0)
    )
    return total if total > 0 else None

def _fmt_dur(seconds: int) -> str:
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    parts  = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"

def _now_ts() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)

# ---- JSON persistence --------------------------------------------------------

def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def _save(data: dict, path: str, lock: asyncio.Lock = None):
    def _do_save(snap):
        os.makedirs("data", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, indent=2)
        try:
            os.replace(tmp, path)
        except OSError:
            pass

    try:
        loop = asyncio.get_running_loop()
        snap = copy.deepcopy(data)
        async def _async_save():
            if lock:
                async with lock:
                    await asyncio.to_thread(_do_save, snap)
            else:
                await asyncio.to_thread(_do_save, snap)
        loop.create_task(_async_save())
    except RuntimeError:
        _do_save(data)

# ---- Config helpers ----------------------------------------------------------

def _antinuke_cfg(data: dict, guild_id: int) -> dict:
    key = str(guild_id)
    if key not in data:
        data[key] = {
            "enabled":    False,
            "thresholds": dict(DEFAULT_ANTINUKE_THRESHOLDS),
            "decay":      DEFAULT_DECAY_S,
            "window":     DEFAULT_WINDOW_S,
        }
    else:
        data[key].setdefault("thresholds", dict(DEFAULT_ANTINUKE_THRESHOLDS))
        data[key].setdefault("decay",  DEFAULT_DECAY_S)
        data[key].setdefault("window", DEFAULT_WINDOW_S)
    return data[key]

def _automod_cfg(data: dict, guild_id: int) -> dict:
    key = str(guild_id)
    if key not in data:
        data[key] = {
            "enabled":     False,
            "spam_count":  DEFAULT_SPAM_COUNT,
            "spam_window": DEFAULT_SPAM_WINDOW,
            "timeout_s":   DEFAULT_TIMEOUT_S,
            "warn_expire": DEFAULT_WARN_EXPIRE,
            "mentions_limit": 5,
            "anti_invite": False,
            "banned_words": [],
        }
    else:
        data[key].setdefault("spam_count",  DEFAULT_SPAM_COUNT)
        data[key].setdefault("spam_window", DEFAULT_SPAM_WINDOW)
        data[key].setdefault("timeout_s",   DEFAULT_TIMEOUT_S)
        data[key].setdefault("warn_expire", DEFAULT_WARN_EXPIRE)
        data[key].setdefault("mentions_limit", 5)
        data[key].setdefault("anti_invite", False)
        data[key].setdefault("banned_words", [])
    return data[key]

# ---- Embed factory -----------------------------------------------------------

def _base_embed(title: str, colour: discord.Colour, description: str = None) -> discord.Embed:
    e = discord.Embed(title=title, colour=colour, timestamp=_now_ts())
    if description:
        e.description = description
    return e

def _status_bar(enabled: bool) -> str:
    return "```\n[ ACTIVE ]  Protection is ON\n```" if enabled else "```\n[ INACTIVE ]  Protection is OFF\n```"

def _err(text: str) -> discord.Embed:
    return _base_embed(f"{ICO_ERROR}  Error", COL_ERROR, text)

def _ok(text: str) -> discord.Embed:
    return _base_embed(f"{ICO_SUCCESS}  Done", COL_SUCCESS, text)


# ---- Bypass Key UI Views -----------------------------------------------------

_UNSET = object()   # sentinel for "not yet selected" in key config


class _KeyDurationSelect(discord.ui.Select):
    """Select menu for bypass key duration."""

    def __init__(self):
        options = [
            discord.SelectOption(label="1 Hour",   value="3600",    emoji="⏱️"),
            discord.SelectOption(label="6 Hours",  value="21600",   emoji="⏱️"),
            discord.SelectOption(label="24 Hours", value="86400",   emoji="⏱️"),
            discord.SelectOption(label="7 Days",   value="604800",  emoji="⏱️"),
            discord.SelectOption(label="30 Days",  value="2592000", emoji="⏱️"),
        ]
        super().__init__(placeholder="Select key duration…", options=options,
                         min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.view._duration = int(self.values[0])
        await interaction.response.defer()


class _KeyMaxUsesSelect(discord.ui.Select):
    """Select menu for bypass key max uses."""

    def __init__(self):
        options = [
            discord.SelectOption(label="1 Use",     value="1",  emoji="🔢"),
            discord.SelectOption(label="3 Uses",    value="3",  emoji="🔢"),
            discord.SelectOption(label="5 Uses",    value="5",  emoji="🔢"),
            discord.SelectOption(label="10 Uses",   value="10", emoji="🔢"),
            discord.SelectOption(label="Unlimited", value="0",  emoji="♾️"),
        ]
        super().__init__(placeholder="Select max uses…", options=options,
                         min_values=1, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        v = int(self.values[0])
        self.view._max_uses = v if v > 0 else None
        await interaction.response.defer()


class _KeyIdleSelect(discord.ui.Select):
    """Select menu for bypass key idle expiry."""

    def __init__(self):
        options = [
            discord.SelectOption(label="30 Minutes", value="1800",  emoji="💤"),
            discord.SelectOption(label="1 Hour",     value="3600",  emoji="💤"),
            discord.SelectOption(label="6 Hours",    value="21600", emoji="💤"),
            discord.SelectOption(label="Never",      value="0",     emoji="♾️"),
        ]
        super().__init__(placeholder="Select idle expiry…", options=options,
                         min_values=1, max_values=1, row=2)

    async def callback(self, interaction: discord.Interaction):
        v = int(self.values[0])
        self.view._idle_window = v if v > 0 else None
        await interaction.response.defer()


class _KeyScopeSelect(discord.ui.Select):
    """Select menu for bypass key scope."""

    def __init__(self):
        options = [
            discord.SelectOption(label="AutoMod Only",  value="automod",  emoji=ICO_AUTOMOD),
            discord.SelectOption(label="Antinuke Only", value="antinuke", emoji=ICO_ANTINUKE),
            discord.SelectOption(label="Both",          value="both",     emoji=ICO_SHIELD),
        ]
        super().__init__(placeholder="Select bypass scope…", options=options,
                         min_values=1, max_values=1, row=3)

    async def callback(self, interaction: discord.Interaction):
        self.view._scope = self.values[0]
        await interaction.response.defer()


class _KeyGenerateView(discord.ui.View):
    """DM view for configuring and generating a bypass key."""

    def __init__(self, cog, guild_id: int, creator_id: int):
        super().__init__(timeout=300)
        self.cog        = cog
        self.guild_id   = guild_id
        self.creator_id = creator_id
        self._message   = None  # set after send for on_timeout editing

        self._duration    = _UNSET
        self._max_uses    = _UNSET
        self._idle_window = _UNSET
        self._scope       = _UNSET

        self.add_item(_KeyDurationSelect())
        self.add_item(_KeyMaxUsesSelect())
        self.add_item(_KeyIdleSelect())
        self.add_item(_KeyScopeSelect())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self._message:
            try:
                embed = _base_embed(f"{ICO_KEY}  Session Expired", COL_MUTED,
                                    "This key generation session has timed out.\nRun `!generatekey` again to start a new one.")
                embed.set_footer(text="Paladin Bypass Keys")
                await self._message.edit(embed=embed, view=self)
            except (discord.HTTPException, discord.NotFound):
                pass

    @discord.ui.button(label="Generate Key", style=discord.ButtonStyle.green, emoji=ICO_KEY, row=4)
    async def generate_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator_id:
            return await interaction.response.send_message(
                embed=_err("This is not your key generation session."), ephemeral=True)

        missing = []
        if self._duration is _UNSET:    missing.append("Duration")
        if self._max_uses is _UNSET:    missing.append("Max Uses")
        if self._idle_window is _UNSET: missing.append("Idle Expiry")
        if self._scope is _UNSET:       missing.append("Scope")
        if missing:
            return await interaction.response.send_message(
                embed=_err(f"Please select: **{', '.join(missing)}**"), ephemeral=True)

        now            = time.time()
        token          = secrets.token_urlsafe(16)
        key_str        = f"PAL-{token}"
        expires_at     = now + self._duration
        idle_expires_at = (now + self._idle_window) if self._idle_window else None

        key_data = {
            "key":             key_str,
            "guild_id":        self.guild_id,
            "created_by":      self.creator_id,
            "scope":           self._scope,
            "max_uses":        self._max_uses,
            "uses_left":       self._max_uses,
            "expires_at":      expires_at,
            "idle_expires_at": idle_expires_at,
            "_idle_window":    self._idle_window,
            "redeemers":       [],
        }

        self.cog._keys.setdefault("keys", []).append(key_data)
        _save(self.cog._keys, KEYS_FILE, self.cog._save_lock)

        guild      = self.cog.bot.get_guild(self.guild_id)
        guild_name = guild.name if guild else f"Guild {self.guild_id}"

        embed = _base_embed(f"{ICO_KEY}  Bypass Key Generated", COL_SUCCESS)
        embed.description = (
            f"Your bypass key for **{guild_name}** has been generated.\n"
            f"**Share this key ONLY with trusted users.**\n\n"
            f"```\n{key_str}\n```"
        )
        embed.add_field(name="Scope",       value=f"`{self._scope.title()}`",     inline=True)
        embed.add_field(name="Duration",    value=f"`{_fmt_dur(self._duration)}`", inline=True)
        embed.add_field(
            name="Max Uses",
            value=f"`{'Unlimited' if self._max_uses is None else self._max_uses}`",
            inline=True,
        )
        embed.add_field(
            name="Idle Expiry",
            value=f"`{'Never' if self._idle_window is None else _fmt_dur(self._idle_window)}`",
            inline=True,
        )
        embed.add_field(name="Expires At", value=f"<t:{int(expires_at)}:R>", inline=True)
        embed.set_footer(text="Use !usekey <key> in the server  |  Paladin Bypass Keys")

        inv_view = _KeyInvalidateView(self.cog, key_str)
        self.cog.bot.add_view(inv_view)

        await interaction.response.edit_message(embed=embed, view=inv_view)
        self.stop()


class _KeyInvalidateView(discord.ui.View):
    """Persistent DM view for invalidating a generated bypass key."""

    def __init__(self, cog, key_str: str):
        super().__init__(timeout=None)
        self.cog     = cog
        self.key_str = key_str

        btn = discord.ui.Button(
            label="Invalidate Key",
            style=discord.ButtonStyle.red,
            emoji="🗑️",
            custom_id=f"pal_inv:{key_str}",
        )
        btn.callback = self._on_invalidate
        self.add_item(btn)

    async def _on_invalidate(self, interaction: discord.Interaction):
        key_data = None
        for k in self.cog._keys.get("keys", []):
            if k["key"] == self.key_str:
                key_data = k
                break

        now = time.time()
        already_dead = (
            key_data is None
            or (key_data.get("uses_left") is not None and key_data["uses_left"] <= 0)
            or (key_data.get("expires_at") is not None and now >= key_data["expires_at"])
            or (key_data.get("idle_expires_at") is not None and now >= key_data["idle_expires_at"])
        )
        if already_dead:
            for item in self.children:
                item.disabled = True
                item.label    = "Key Invalidated"
            embed = _base_embed(f"{ICO_KEY}  Already Invalidated", COL_MUTED,
                                "This key was already invalidated or removed.")
            embed.set_footer(text="Paladin Bypass Keys")
            return await interaction.response.edit_message(embed=embed, view=self)

        if interaction.user.id != key_data["created_by"]:
            return await interaction.response.send_message(
                embed=_err("Only the key creator can invalidate this key."), ephemeral=True)

        key_data["uses_left"] = 0
        to_remove = [k for k, v in self.cog._key_bypass.items() if v.get("key") == self.key_str]
        for k in to_remove:
            del self.cog._key_bypass[k]

        _save(self.cog._keys, KEYS_FILE, self.cog._save_lock)

        for item in self.children:
            item.disabled = True
            item.label    = "Key Invalidated"

        embed = _base_embed(f"{ICO_KEY}  Key Invalidated", COL_DANGER)
        embed.description = (
            f"The bypass key has been **permanently invalidated**.\n"
            f"**{len(to_remove)}** active bypass(es) were revoked."
        )
        embed.set_footer(text="Paladin Bypass Keys")
        await interaction.response.edit_message(embed=embed, view=self)


# ==============================================================================
#  COG
# ==============================================================================

class Paladin(commands.Cog):
    """Paladin -- Antinuke + AutoMod protection system."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot

        # Antinuke state
        self._an_data: dict = _load(DATA_FILE)
        self._wl:      dict = _load(WHITELIST_FILE)
        self._action_log: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(deque)))
        self._seen_entries: deque = deque(maxlen=5000)
        self._seen_entries_set: set = set()
        self._pending_obj: dict = {}

        # AutoMod state
        self._am_data: dict = _load(AUTOMOD_DATA_FILE)
        self._am_strikes: dict = defaultdict(dict)
        self._restore_strikes()

        self._msg_times: dict = defaultdict(lambda: defaultdict(deque))

        # AlertNuke subscribers
        self._alertnuke: dict = _load(ALERTNUKE_FILE)

        self._save_lock: asyncio.Lock = asyncio.Lock()

        # Bypass key state
        self._keys: dict = _load(KEYS_FILE)
        self._key_bypass: dict = {}  # {(guild_id, user_id): {"scope", "valid_until", "key"}}
        self._am_locks: dict = {}

        # Fix 3: regex pattern cache — {guild_id: (frozenset(words), compiled_pattern)}
        # The pattern is only recompiled when the banned-word list has actually changed,
        # not on every incoming message.  Under raid conditions this eliminates the
        # synchronous re.compile() call that would otherwise saturate the CPU thread.
        self._banned_pattern_cache: dict[int, tuple[frozenset, re.Pattern | None]] = {}

        self._cleanup_task.start()
        self._key_expiry_task.start()

        # Register persistent invalidation views for active keys
        _now = time.time()
        for _kd in self._keys.get("keys", []):
            if _kd.get("uses_left") is not None and _kd["uses_left"] <= 0:
                continue
            if _kd.get("expires_at") is not None and _now >= _kd["expires_at"]:
                continue
            self.bot.add_view(_KeyInvalidateView(self, _kd["key"]))

    def cog_unload(self):
        self._cleanup_task.cancel()
        self._key_expiry_task.cancel()

    def _get_am_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        if key not in self._am_locks:
            self._am_locks[key] = asyncio.Lock()
        return self._am_locks[key]

    def _get_banned_pattern(self, guild_id: int, cfg: dict) -> re.Pattern | None:
        """Return a compiled regex for alpha banned-words, recompiling only when the
        word list has changed.  This prevents re.compile() being called on every
        message (Fix 3 — CPU starvation via re-compiled regex)."""
        alpha_words = [w for w in cfg.get("banned_words", []) if re.search(r'\w', w)]
        if not alpha_words:
            return None
        key_set = frozenset(alpha_words)
        cached  = self._banned_pattern_cache.get(guild_id)
        if cached and cached[0] == key_set:
            return cached[1]
        pattern = re.compile(r'\b(?:' + "|".join(re.escape(w) for w in alpha_words) + r')\b')
        self._banned_pattern_cache[guild_id] = (key_set, pattern)
        return pattern

    # ---- Strike persistence --------------------------------------------------

    def _restore_strikes(self):
        now = time.time()
        for gid_s, users in self._am_data.get("strikes", {}).items():
            gid    = int(gid_s)
            cfg    = _automod_cfg(self._am_data, gid)
            expire = cfg["warn_expire"]
            for uid_s, entry in users.items():
                if now - entry.get("last", 0) < expire:
                    self._am_strikes[gid][int(uid_s)] = entry

    def _persist_strikes(self):
        self._am_data["strikes"] = {
            str(gid): {str(uid): v for uid, v in users.items()}
            for gid, users in self._am_strikes.items()
        }
        _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)

    @tasks.loop(minutes=10)
    async def _cleanup_task(self):
        now     = time.time()
        changed = False
        for gid in list(self._am_strikes.keys()):
            cfg    = _automod_cfg(self._am_data, gid)
            expire = cfg["warn_expire"]
            for uid in list(self._am_strikes[gid].keys()):
                if now - self._am_strikes[gid][uid].get("last", 0) >= expire:
                    del self._am_strikes[gid][uid]
                    changed = True
        if changed:
            self._persist_strikes()

        # Fix 4: Purge _msg_times entries for users who have been idle longer than
        # the spam window.  Without this, every unique user ID accumulates a deque
        # in memory for the entire bot uptime.  Same for _am_locks — discard lock
        # objects whose owning user has no recent activity (locks are cheap to
        # recreate on demand via _get_am_lock).
        spam_window_max = 60  # generous upper bound; real window is cfg["spam_window"]
        for gid in list(self._msg_times.keys()):
            for uid in list(self._msg_times[gid].keys()):
                q = self._msg_times[gid][uid]
                # Drop empty or fully-expired queues
                while q and now - q[0] > spam_window_max:
                    q.popleft()
                if not q:
                    del self._msg_times[gid][uid]
            if not self._msg_times[gid]:
                del self._msg_times[gid]

        # Purge per-user Lock objects for users with no remaining msg_times entry
        # (the lock is not held at this point because we only reach here between events)
        active_keys = {
            (gid, uid)
            for gid, users in self._msg_times.items()
            for uid in users
        }
        for key in list(self._am_locks.keys()):
            if key not in active_keys:
                lock = self._am_locks[key]
                if not lock.locked():
                    del self._am_locks[key]

    @_cleanup_task.before_loop
    async def _cleanup_task_before(self):
        await self.bot.wait_until_ready()

    # ---- Shared helpers ------------------------------------------------------

    def _can_manage(self, user, guild: discord.Guild) -> bool:
        return user.id in OWNERS or user.id == guild.owner_id

    def _is_protected(self, guild_id: int, user_id: int, *, scope: str = None) -> bool:
        if user_id in OWNERS:
            return True
        guild = self.bot.get_guild(guild_id)
        if guild and user_id == guild.owner_id:
            return True
        if user_id in self._wl.get(str(guild_id), []):
            return True
        # Bypass key check (only when a specific scope is queried)
        if scope is not None:
            bypass = self._key_bypass.get((guild_id, user_id))
            if bypass and (bypass["scope"] == "both" or bypass["scope"] == scope):
                return True
        return False

    def _bypass_reason(self, guild_id: int, user_id: int) -> str:
        """Return a human-readable string explaining WHY a user is protected."""
        if user_id in OWNERS:
            return "Hardcoded Bot Owner"
        guild = self.bot.get_guild(guild_id)
        if guild and user_id == guild.owner_id:
            return "Server Owner"
        if user_id in self._wl.get(str(guild_id), []):
            return "Whitelisted User"
        if (guild_id, user_id) in self._key_bypass:
            return "Bypass Key"
        return "Unknown"

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        logging_cog = self.bot.cogs.get("Logging")
        if logging_cog:
            await logging_cog._log(guild.id, embed)
            return
        ch = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel)
                      and any(k in c.name.lower() for k in ("log", "mod", "audit"))
                      and c.permissions_for(guild.me).send_messages,
            guild.text_channels,
        )
        if ch:
            await ch.send(embed=embed)

    # ---- AlertNuke helpers ---------------------------------------------------

    def _alertnuke_list(self, guild_id: int) -> list:
        return self._alertnuke.get(str(guild_id), [])

    def _alertnuke_toggle(self, guild_id: int, user_id: int) -> bool:
        key = str(guild_id)
        self._alertnuke.setdefault(key, [])
        if user_id in self._alertnuke[key]:
            self._alertnuke[key].remove(user_id)
            _save(self._alertnuke, ALERTNUKE_FILE, self._save_lock)
            return False
        self._alertnuke[key].append(user_id)
        _save(self._alertnuke, ALERTNUKE_FILE, self._save_lock)
        return True

    async def _alertnuke_dm(self, guild: discord.Guild, embed: discord.Embed):
        subscribers = self._alertnuke_list(guild.id)
        if not subscribers:
            return
        for uid in subscribers:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                await user.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass

    # ==========================================================================
    #  LAYER 1 -- ANTINUKE
    # ==========================================================================

    def _an_cfg(self, guild_id: int) -> dict:
        key    = str(guild_id)
        is_new = key not in self._an_data
        cfg    = _antinuke_cfg(self._an_data, guild_id)
        if is_new:
            _save(self._an_data, DATA_FILE, self._save_lock)
        return cfg

    def _an_enabled(self, guild_id: int) -> bool:
        return _antinuke_cfg(self._an_data, guild_id).get("enabled", False)

    def _an_threshold(self, guild_id: int, action: str) -> int:
        return _antinuke_cfg(self._an_data, guild_id)["thresholds"].get(
            action, DEFAULT_ANTINUKE_THRESHOLDS.get(action, 2)
        )

    def _an_window(self, guild_id: int) -> int:
        return _antinuke_cfg(self._an_data, guild_id).get("window", DEFAULT_WINDOW_S)

    def _an_decay(self, guild_id: int) -> int:
        return _antinuke_cfg(self._an_data, guild_id).get("decay", DEFAULT_DECAY_S)

    def _record_action(self, guild_id: int, action: str, user_id: int) -> int:
        now    = time.monotonic()
        window = self._an_window(guild_id)
        q      = self._action_log[guild_id][action][user_id]
        q.append(now)
        while q and now - q[0] > window:
            q.popleft()
        return len(q)

    async def _an_punish(self, guild: discord.Guild, actor: discord.Member, action: str, count: int):
        # ------------------------------------------------------------------
        # Special path 1: A bot was just added (bot_add threshold).
        #
        # Two cases:
        #   A) Unauthorised inviter (not owner/whitelisted):
        #      → Ban the bot AND strip the inviter's roles.
        #   B) Authorised inviter (owner/whitelisted) but bot itself is not:
        #      → Ban the bot ONLY. The inviter is trusted; their roles are safe.
        # ------------------------------------------------------------------
        if action == "bot_add":
            pending_bots = self._pending_obj.get(guild.id, {}).pop("bot_add", [])
            if guild.id in self._pending_obj and not self._pending_obj[guild.id]:
                del self._pending_obj[guild.id]
            self._action_log[guild.id][action][actor.id].clear()

            banned_bots: list = []
            ban_failed_bots: list = []
            for bot_member in pending_bots:
                if isinstance(bot_member, discord.Member) and bot_member.bot:
                    try:
                        await guild.ban(bot_member, reason=f"[Paladin Antinuke] Unauthorised bot add by {actor} ({actor.id})")
                        banned_bots.append(bot_member)
                    except (discord.Forbidden, discord.HTTPException):
                        ban_failed_bots.append(bot_member)

            inviter_protected  = self._is_protected(guild.id, actor.id, scope="antinuke")
            inviter_bypass_reason = self._bypass_reason(guild.id, actor.id) if inviter_protected else None

            roles_removed: list = []
            strip_failed        = False

            if not inviter_protected:
                is_member = isinstance(actor, discord.Member)
                roles_to_remove = [
                    r for r in (actor.roles if is_member else [])
                    if r != guild.default_role and guild.me.top_role > r and not r.managed
                ]
                if roles_to_remove:
                    try:
                        await actor.remove_roles(*roles_to_remove, reason=f"[Paladin Antinuke] Invited unauthorised bot(s) x{count}")
                        roles_removed = roles_to_remove
                    except (discord.Forbidden, discord.HTTPException):
                        strip_failed = True

            log_embed = self._an_bot_add_embed(guild, actor, count, banned_bots, ban_failed_bots, roles_removed, strip_failed, inviter_protected, inviter_bypass_reason)
            await self._log(guild, log_embed)

            alert_embed = self._alertnuke_bot_add_embed(guild, actor, count, banned_bots, ban_failed_bots, roles_removed, strip_failed, inviter_protected, inviter_bypass_reason)
            await self._alertnuke_dm(guild, alert_embed)
            return

        # ------------------------------------------------------------------
        # Special path 2: A BOT triggered the antinuke (e.g. rogue bot nuking)
        # Ban the bot immediately. Then find the original inviter via audit log
        # and strip their roles (unless they are protected).
        # ------------------------------------------------------------------
        if actor.bot:
            # Ban the rogue bot IMMEDIATELY to stop the nuke
            bot_banned = False
            try:
                await guild.ban(actor, reason=f"[Paladin Antinuke] Rogue bot triggered '{action}' threshold")
                bot_banned = True
            except (discord.Forbidden, discord.HTTPException):
                pass

            # Clear ALL action buckets for this bot across every action type so that
            # if it is ever unbanned and re-invited it starts with a clean slate
            # (not just the single triggering action).
            for _action_bucket in self._action_log.get(guild.id, {}).values():
                _action_bucket.pop(actor.id, None)

            pending_objs = self._pending_obj.get(guild.id, {}).pop(action, [])
            if guild.id in self._pending_obj and not self._pending_obj[guild.id]:
                del self._pending_obj[guild.id]

            reversion_cog = self.bot.cogs.get("Reversion")
            reversed_ok   = False
            if reversion_cog:
                try:
                    for pending_obj in (pending_objs or [None]):
                        await reversion_cog.on_paladin_action_reversed(guild, actor, action, pending_obj)
                    reversed_ok = True
                except Exception:
                    reversed_ok = False

            # ---- Find the original inviter via audit log ----
            inviter               = None
            inviter_protected     = False
            inviter_bypass_reason = None
            inviter_roles_removed = []
            inviter_strip_failed  = False

            try:
                async for entry in guild.audit_logs(limit=20, action=discord.AuditLogAction.bot_add):
                    if entry.target and entry.target.id == actor.id:
                        inv = guild.get_member(entry.user.id)
                        if inv is None:
                            try: inv = await guild.fetch_member(entry.user.id)
                            except (discord.NotFound, discord.HTTPException): inv = entry.user
                        inviter = inv
                        break
            except (discord.Forbidden, discord.HTTPException):
                pass

            if inviter is not None:
                inviter_protected     = self._is_protected(guild.id, inviter.id, scope="antinuke")
                inviter_bypass_reason = self._bypass_reason(guild.id, inviter.id) if inviter_protected else None

                # Protected inviter (owner / whitelisted / hardcoded owner):
                # The bot went rogue on its own — the inviter is trusted, don't touch them.
                if not inviter_protected and isinstance(inviter, discord.Member):
                    roles_to_remove = [
                        r for r in inviter.roles
                        if r != guild.default_role and guild.me.top_role > r and not r.managed
                    ]
                    if roles_to_remove:
                        try:
                            await inviter.remove_roles(*roles_to_remove, reason=f"[Paladin Antinuke] Invited rogue bot {actor} ({actor.id})")
                            inviter_roles_removed = roles_to_remove
                        except (discord.Forbidden, discord.HTTPException):
                            inviter_strip_failed = True

            log_embed = self._an_bot_nuking_embed(guild, actor, action, count, bot_banned, inviter, inviter_protected, inviter_bypass_reason, inviter_roles_removed, inviter_strip_failed)
            await self._log(guild, log_embed)

            alert_embed = self._alertnuke_bot_nuking_embed(guild, actor, action, count, bot_banned, inviter, inviter_protected, inviter_bypass_reason, inviter_roles_removed, inviter_strip_failed, reversed_ok)
            await self._alertnuke_dm(guild, alert_embed)
            return

        # ------------------------------------------------------------------
        # Standard path: HUMAN triggered the antinuke (Strip roles only)
        # ------------------------------------------------------------------
        is_member = isinstance(actor, discord.Member)
        roles_to_remove = [
            r for r in (actor.roles if is_member else [])
            if r != guild.default_role and guild.me.top_role > r and not r.managed
        ]

        strip_failed = False
        if roles_to_remove:
            try:
                await actor.remove_roles(
                    *roles_to_remove,
                    reason=f"[Paladin Antinuke] {_action_label(action)} x{count}"
                )
            except (discord.Forbidden, discord.HTTPException):
                strip_failed  = True
                roles_to_remove = []

        self._action_log[guild.id][action][actor.id].clear()

        log_embed = self._an_punish_embed(actor, guild, action, count, roles_to_remove, strip_failed=strip_failed)
        await self._log(guild, log_embed)

        pending_objs = self._pending_obj.get(guild.id, {}).pop(action, [])
        if guild.id in self._pending_obj and not self._pending_obj[guild.id]:
            del self._pending_obj[guild.id]

        reversion_cog = self.bot.cogs.get("Reversion")
        reversed_ok   = False
        if reversion_cog:
            try:
                for pending_obj in (pending_objs or [None]):
                    await reversion_cog.on_paladin_action_reversed(guild, actor, action, pending_obj)
                reversed_ok = True
            except Exception:
                reversed_ok = False

        alert_embed = self._alertnuke_antinuke_embed(guild, actor, action, count, roles_to_remove, reversed_ok)
        await self._alertnuke_dm(guild, alert_embed)

    # ---- Embed: Bot was actively nuking (Log) -------------------------------
    def _an_bot_nuking_embed(self, guild, actor, action, count, bot_banned, inviter, inviter_protected, inviter_bypass_reason, roles_removed, strip_failed) -> discord.Embed:
        embed = _base_embed(f"{ICO_SHIELD}  Antinuke — Rogue Bot Caught", COL_CRITICAL)
        embed.set_author(name=f"{actor} ({actor.id})", icon_url=actor.display_avatar.url)
        embed.set_thumbnail(url=actor.display_avatar.url)
        
        embed.description = f"> 🤖 **A bot triggered the antinuke!**\n> Action: `{_action_label(action)}` x`{count}`"
        
        embed.add_field(name="Bot Punishment", value="✅ **BANNED**" if bot_banned else "⚠️ **Ban Failed** (Missing perms)", inline=False)
        
        if inviter:
            embed.add_field(name="Invited By", value=f"{inviter.mention} (`{inviter.id}`)", inline=False)
            if inviter_protected:
                embed.add_field(name=f"{ICO_WARN} Inviter Punishment: Spared", value=f"Exempt from punishment ({inviter_bypass_reason}).", inline=False)
            elif strip_failed:
                embed.add_field(name=f"⚠️ Inviter Punishment: Strip Failed", value="Attempted to strip roles but failed.", inline=False)
            else:
                r_list = ", ".join(r.mention for r in roles_removed) if roles_removed else "*None*"
                if len(r_list) > 1024: r_list = r_list[:1021] + "..."
                embed.add_field(name=f"{ICO_STRIP} Inviter Punishment: Roles Stripped", value=f"**{len(roles_removed)}** role(s) removed:\n{r_list}", inline=False)
        else:
            embed.add_field(name="Invited By", value="*Could not find inviter in recent audit logs (log expired or missing).*")

        embed.set_footer(text=f"Paladin Antinuke  |  Guild: {guild.name}")
        return embed

    # ---- Embed: Bot was actively nuking (DM Alert) --------------------------
    def _alertnuke_bot_nuking_embed(self, guild, actor, action, count, bot_banned, inviter, inviter_protected, inviter_bypass_reason, roles_removed, strip_failed, reversed_ok) -> discord.Embed:
        embed = _base_embed(f"🔴  Antinuke Alert — Rogue Bot — {guild.name}", COL_CRITICAL)
        try: embed.set_author(name=f"{actor} ({actor.id})", icon_url=actor.display_avatar.url)
        except: embed.set_author(name=f"ID: {actor.id}")
        
        embed.description = f"> 🤖 **A bot triggered the antinuke in {guild.name}!**"
        
        embed.add_field(name="Trigger", value=f"`{_action_label(action)}`", inline=True)
        embed.add_field(name="Actions in Window", value=f"`{count}`", inline=True)
        embed.add_field(name="Bot Outcome", value="✅ **Banned**" if bot_banned else "⚠️ **Ban Failed**", inline=True)
        
        if inviter:
            embed.add_field(name="Invited By", value=f"{inviter.mention} (`{inviter.id}`)", inline=False)
            if inviter_protected:
                embed.add_field(name=f"{ICO_WARN} Inviter Punishment: Spared", value=f"Exempt from punishment ({inviter_bypass_reason}).", inline=False)
            elif strip_failed:
                embed.add_field(name=f"⚠️ Inviter Punishment: Strip Failed", value="Attempted to strip roles but failed.", inline=False)
            else:
                r_list = ", ".join(f"`{r.name}`" for r in roles_removed) if roles_removed else "*None*"
                if len(r_list) > 1024: r_list = r_list[:1021] + "..."
                embed.add_field(name=f"{ICO_STRIP} Inviter Punishment: Roles Stripped", value=f"**{len(roles_removed)}** role(s) removed:\n{r_list}", inline=False)
        else:
            embed.add_field(name="Invited By", value="*Could not find inviter in recent audit logs.*", inline=False)
            
        embed.add_field(name="Reversion", value="✅ Reversed" if reversed_ok else "⚠️ No reversion (cog not loaded/failed)", inline=False)
        
        embed.set_footer(text=f"Paladin AlertNuke  |  {guild.name}")
        return embed

    # ---- Embed: standard antinuke punish ------------------------------------

    def _an_punish_embed(
        self,
        actor,
        guild: discord.Guild,
        action: str,
        count: int,
        roles: list,
        strip_failed: bool = False,
    ) -> discord.Embed:
        embed = _base_embed(
            f"{ICO_SHIELD}  Antinuke — Roles Stripped",
            COL_CRITICAL,
        )
        embed.set_author(name=f"{actor} ({actor.id})", icon_url=actor.display_avatar.url)
        embed.set_thumbnail(url=actor.display_avatar.url)
        embed.description = (
            f"> {ICO_STRIP} **{actor.mention} lost all roles** after triggering the antinuke threshold."
            + ("\n> ⚠️ Role strip failed (missing permissions)." if strip_failed else "")
        )
        embed.add_field(name="Offending Action",   value=f"`{_action_label(action)}`", inline=True)
        embed.add_field(name="Actions in Window",  value=f"`{count}`",                 inline=True)
        embed.add_field(name="Roles Removed",      value=f"`{len(roles)}`",             inline=True)

        role_list = ", ".join(r.mention for r in roles) if roles else ("None" if not strip_failed else "Strip failed")
        if len(role_list) > 1024:
            role_list = role_list[:1021] + "..."
        embed.add_field(name="Stripped Roles", value=role_list, inline=False)
        embed.set_footer(text=f"Paladin Antinuke  |  Guild: {guild.name}")
        return embed

    # ---- Embed: bot_add unified log embed -----------------------------------

    def _an_bot_add_embed(
        self,
        guild: discord.Guild,
        actor,
        count: int,
        banned_bots: list,
        ban_failed_bots: list,
        roles_removed: list,
        strip_failed: bool,
        inviter_protected: bool,
        inviter_bypass_reason: str | None,
    ) -> discord.Embed:
        embed = _base_embed(
            f"{ICO_SHIELD}  Antinuke — Unauthorised Bot Add",
            COL_CRITICAL,
        )
        embed.set_author(name=f"{actor} ({actor.id})", icon_url=actor.display_avatar.url)
        embed.set_thumbnail(url=actor.display_avatar.url)

        lines = [f"> 🤖 **Unauthorised bot addition detected** in **{guild.name}**."]
        if banned_bots:
            lines.append(f"> 🔨 **{len(banned_bots)} bot(s) banned** successfully.")
        if ban_failed_bots:
            lines.append(f"> ⚠️ **{len(ban_failed_bots)} bot(s) could not be banned** (missing permissions).")
        embed.description = "\n".join(lines)

        inviter_val = f"{actor.mention}\n`{actor}` (`{actor.id}`)"
        embed.add_field(name="Inviter", value=inviter_val, inline=True)
        embed.add_field(name="Bot Adds in Window", value=f"`{count}`", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        if banned_bots:
            bot_lines = "\n".join(f"• {b.mention} `{b}` (`{b.id}`)" for b in banned_bots)
            if len(bot_lines) > 1024:
                bot_lines = bot_lines[:1021] + "..."
            embed.add_field(name="🔨 Bots Banned", value=bot_lines, inline=False)

        if ban_failed_bots:
            fail_lines = "\n".join(f"• {b.mention} `{b}` (`{b.id}`)" for b in ban_failed_bots)
            if len(fail_lines) > 1024:
                fail_lines = fail_lines[:1021] + "..."
            embed.add_field(name="⚠️ Ban Failed", value=fail_lines, inline=False)

        # Explicitly formatted Inviter fields so you know exactly what triggered
        if inviter_protected:
            embed.add_field(
                name=f"{ICO_WARN} Inviter Punishment: Spared",
                value=f"`{actor}` is a **{inviter_bypass_reason}** and exempt from punishment.",
                inline=False,
            )
        elif strip_failed:
            embed.add_field(
                name=f"⚠️ Inviter Punishment: Strip Failed",
                value=f"Attempted to strip `{actor}` but failed (missing permissions or hierarchy issue).",
                inline=False,
            )
        else:
            role_list = ", ".join(r.mention for r in roles_removed) if roles_removed else "*No roles to remove*"
            if len(role_list) > 1024:
                role_list = role_list[:1021] + "..."
            embed.add_field(
                name=f"{ICO_STRIP} Inviter Punishment: Roles Stripped",
                value=f"**{len(roles_removed)}** role(s) removed from `{actor}`:\n{role_list}",
                inline=False,
            )

        embed.set_footer(text=f"Paladin Antinuke  |  Guild: {guild.name}")
        return embed

    # ---- Embed: bot_add alert DM embed --------------------------------------

    def _alertnuke_bot_add_embed(
        self,
        guild: discord.Guild,
        actor,
        count: int,
        banned_bots: list,
        ban_failed_bots: list,
        roles_removed: list,
        strip_failed: bool,
        inviter_protected: bool,
        inviter_bypass_reason: str | None,
    ) -> discord.Embed:
        embed = _base_embed(
            f"🔴  Antinuke Alert — Bot Add — {guild.name}",
            COL_CRITICAL,
        )
        try:
            embed.set_author(name=f"{actor} ({actor.id})", icon_url=actor.display_avatar.url)
        except Exception:
            embed.set_author(name=f"ID: {actor.id}")

        lines = [f"> 🤖 **Unauthorised bot addition detected** in **{guild.name}**."]
        if banned_bots:
            lines.append(f"> 🔨 **{len(banned_bots)} bot(s) banned** successfully.")
        if ban_failed_bots:
            lines.append(f"> ⚠️ **{len(ban_failed_bots)} bot(s) could not be banned** (missing permissions).")
        embed.description = "\n".join(lines)

        inviter_val = f"{actor.mention}\n`{actor}` (`{actor.id}`)"
        embed.add_field(name="Inviter", value=inviter_val, inline=True)
        embed.add_field(name="Bot Adds in Window", value=f"`{count}`", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        if banned_bots:
            bot_lines = "\n".join(f"• `{b}` (`{b.id}`)" for b in banned_bots)
            if len(bot_lines) > 1024:
                bot_lines = bot_lines[:1021] + "..."
            embed.add_field(name="🔨 Bots Banned", value=bot_lines, inline=False)

        if ban_failed_bots:
            fail_lines = "\n".join(f"• `{b}` (`{b.id}`)" for b in ban_failed_bots)
            if len(fail_lines) > 1024:
                fail_lines = fail_lines[:1021] + "..."
            embed.add_field(name="⚠️ Ban Failed", value=fail_lines, inline=False)

        # Explicitly formatted Inviter fields for the DM
        if inviter_protected:
            embed.add_field(
                name=f"{ICO_WARN} Inviter Punishment: Spared",
                value=f"`{actor}` is a **{inviter_bypass_reason}** and exempt from punishment.",
                inline=False,
            )
        elif strip_failed:
            embed.add_field(
                name=f"⚠️ Inviter Punishment: Strip Failed",
                value=f"Attempted to strip `{actor}` but failed (missing permissions or hierarchy issue).",
                inline=False,
            )
        else:
            role_list = ", ".join(f"`{r.name}`" for r in roles_removed) if roles_removed else "*No roles to remove*"
            if len(role_list) > 1024:
                role_list = role_list[:1021] + "..."
            embed.add_field(
                name=f"{ICO_STRIP} Inviter Punishment: Roles Stripped",
                value=f"**{len(roles_removed)}** role(s) removed from `{actor}`:\n{role_list}",
                inline=False,
            )

        embed.set_footer(text=f"Paladin AlertNuke  |  {guild.name}")
        return embed

    # ---- Embed: standard antinuke alert DM ----------------------------------

    def _alertnuke_antinuke_embed(
        self,
        guild: discord.Guild,
        actor,
        action: str,
        count: int,
        roles: list,
        reversed_ok: bool,
    ) -> discord.Embed:
        embed = _base_embed(
            f"🔴  Antinuke Alert  —  {guild.name}",
            COL_CRITICAL,
        )
        try:
            embed.set_author(name=f"{actor} ({actor.id})", icon_url=actor.display_avatar.url)
        except Exception:
            embed.set_author(name=f"ID: {actor.id}")
        embed.description = (
            f"> {ICO_STRIP} **Antinuke tripped** in **{guild.name}**.\n"
            f"> Offending user: {actor.mention if hasattr(actor, 'mention') else f'`{actor.id}`'}"
        )
        embed.add_field(name="Trigger",           value=f"`{_action_label(action)}`", inline=True)
        embed.add_field(name="Actions in Window", value=f"`{count}`",                  inline=True)
        embed.add_field(name="Roles Stripped",    value=f"`{len(roles)}`",              inline=True)
        embed.add_field(
            name="Reversion",
            value="✅ Reversed" if reversed_ok else "⚠️ No reversion (cog not loaded or failed)",
            inline=False,
        )
        embed.set_footer(text=f"Paladin AlertNuke  |  {guild.name}")
        return embed

    # ---- Embed: automod alert DM --------------------------------------------

    def _alertnuke_automod_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        strike: int,
        action: str,
        trigger: str,
    ) -> discord.Embed:
        base_action = action.split()[0]
        colours = {"warn": COL_WARNING, "timeout": COL_WARNING, "kick": COL_DANGER, "ban": COL_CRITICAL}
        colour  = colours.get(base_action, COL_DANGER)

        embed = _base_embed(
            f"🟠  AutoMod Alert  —  {guild.name}",
            colour,
        )
        embed.set_author(name=f"{member} ({member.id})", icon_url=member.display_avatar.url)
        embed.description = (
            f"> {ICO_AUTOMOD} **AutoMod action taken** in **{guild.name}**."
        )
        embed.add_field(name="Member",  value=f"{member.mention}\n`{member}`", inline=True)
        embed.add_field(name="Action",  value=f"`{action}`",                   inline=True)
        embed.add_field(name="Strike",  value=f"`{strike} / 4`",               inline=True)
        embed.add_field(name="Trigger", value=f"`{trigger.replace('_', ' ').title()}`", inline=True)
        embed.add_field(
            name="Reversion",
            value="ℹ️ AutoMod actions are not auto-reversed.",
            inline=False,
        )
        embed.set_footer(text=f"Paladin AlertNuke  |  {guild.name}")
        return embed

    async def _register_attempt(self, guild: discord.Guild, actor: discord.Member, action: str, obj=None):
        if not self._an_enabled(guild.id): return

        # For bot_add we ALWAYS proceed — even if the inviter is protected —
        # because the bot itself may be unauthorised and must be banned.
        # The inviter's protection status is evaluated inside _an_punish to decide
        # whether to also strip their roles.
        #
        # For every other action, a protected actor (hardcoded owner / server owner /
        # whitelisted) is fully trusted and we skip them entirely.
        if action != "bot_add" and self._is_protected(guild.id, actor.id, scope="antinuke"):
            return

        count     = self._record_action(guild.id, action, actor.id)
        threshold = self._an_threshold(guild.id, action)

        if count >= threshold:
            log.info(f"[paladin] THRESHOLD HIT — {action} by {actor} ({actor.id}), punishing")
            self._pending_obj.setdefault(guild.id, {}).setdefault(action, [])
            if obj is not None:
                self._pending_obj[guild.id][action].append(obj)
            await self._an_punish(guild, actor, action, count)

    # ---- Public hooks for mod.py ban/kick flows --------------------------------

    def _peek_action_count(self, guild_id: int, action: str, user_id: int) -> int:
        """Return the current in-window count for (user, action) WITHOUT adding a
        new entry.  Safe to call at any time; it only prunes expired timestamps."""
        now    = time.monotonic()
        window = self._an_window(guild_id)
        q      = self._action_log[guild_id][action][user_id]
        while q and now - q[0] > window:
            q.popleft()
        return len(q)

    async def pre_check_mod_action(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        action: str,           # "ban" or "kick"
    ) -> tuple[bool, str]:
        """
        Called by mod.py when the !ban / !kick command is FIRST INVOKED — before the
        confirmation embed is shown.

        Returns (blocked: bool, reason: str).

        • blocked=True  → do NOT show the confirmation embed; show a "blocked by
                           Paladin" message instead.  Roles are stripped here.
        • blocked=False → the action is within threshold; show the confirmation embed.

        The audit-log listener (on_member_ban / on_member_remove) is the SOLE recorder.
        This method never pre-records anything, so there is no double-counting.
        """
        if not self._an_enabled(guild.id):
            return False, "antinuke_disabled"

        if self._is_protected(guild.id, actor.id, scope="antinuke"):
            return False, self._bypass_reason(guild.id, actor.id)

        current_count = self._peek_action_count(guild.id, action, actor.id)
        threshold     = self._an_threshold(guild.id, action)

        # Would this next action breach the threshold?
        if current_count + 1 >= threshold:
            projected = current_count + 1
            log.info(
                f"[paladin] pre_check_mod_action BLOCKED — {action} by {actor} ({actor.id}) "
                f"projected={projected}>={threshold}"
            )

            is_member = isinstance(actor, discord.Member)
            roles_to_remove = [
                r for r in (actor.roles if is_member else [])
                if r != guild.default_role and guild.me.top_role > r and not r.managed
            ]
            strip_failed = False
            if roles_to_remove:
                try:
                    await actor.remove_roles(
                        *roles_to_remove,
                        reason=f"[Paladin Antinuke] {_action_label(action)} x{projected} (blocked at command)"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    strip_failed    = True
                    roles_to_remove = []

            self._action_log[guild.id][action][actor.id].clear()

            log_embed = self._an_punish_embed(actor, guild, action, projected, roles_to_remove, strip_failed=strip_failed)
            await self._log(guild, log_embed)

            alert_embed = self._alertnuke_antinuke_embed(guild, actor, action, projected, roles_to_remove, False)
            await self._alertnuke_dm(guild, alert_embed)

            return True, f"threshold_exceeded:{projected}/{threshold}"

        return False, f"allowed:{current_count + 1}/{threshold}"

    async def check_mod_action(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        action: str,           # "ban" or "kick"
        target: discord.Member | discord.User,
    ) -> tuple[bool, str]:
        """
        Called by mod.py AFTER the user clicks the Confirm button on a !ban / !kick embed.

        This is a SAFETY RE-CHECK only.  pre_check_mod_action() already ran at command
        invocation time and caught the common case.  This re-check guards against the
        race where the actor performed additional actions between invocation and clicking
        Confirm (e.g. they had two confirmation embeds open at once).

        Returns (allowed: bool, reason: str).

        IMPORTANT: This method does NOT pre-record the action.  The audit-log listener
        (on_member_ban / on_member_remove) is the sole recorder, so there is zero
        double-counting regardless of which path is taken.
        """
        if not self._an_enabled(guild.id):
            return True, "antinuke_disabled"

        if self._is_protected(guild.id, actor.id, scope="antinuke"):
            return True, self._bypass_reason(guild.id, actor.id)

        # Peek — do NOT record here; the audit log listener will record when the
        # actual ban/kick fires.
        current_count = self._peek_action_count(guild.id, action, actor.id)
        threshold     = self._an_threshold(guild.id, action)

        if current_count >= threshold:
            # Threshold already breached (race condition between embeds).
            log.info(
                f"[paladin] check_mod_action BLOCKED (race) — {action} by {actor} ({actor.id}) "
                f"count={current_count}>={threshold}"
            )

            is_member = isinstance(actor, discord.Member)
            roles_to_remove = [
                r for r in (actor.roles if is_member else [])
                if r != guild.default_role and guild.me.top_role > r and not r.managed
            ]
            strip_failed = False
            if roles_to_remove:
                try:
                    await actor.remove_roles(
                        *roles_to_remove,
                        reason=f"[Paladin Antinuke] {_action_label(action)} x{current_count} (blocked via confirm)"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    strip_failed    = True
                    roles_to_remove = []

            self._action_log[guild.id][action][actor.id].clear()

            log_embed = self._an_punish_embed(actor, guild, action, current_count, roles_to_remove, strip_failed=strip_failed)
            await self._log(guild, log_embed)

            alert_embed = self._alertnuke_antinuke_embed(guild, actor, action, current_count, roles_to_remove, False)
            await self._alertnuke_dm(guild, alert_embed)

            return False, f"threshold_exceeded:{current_count}/{threshold}"

        # Under threshold — allow.  Do NOT record; the audit log handles it.
        return True, f"allowed:{current_count + 1}/{threshold}"

    # ---- Audit-log listeners -------------------------------------------------

    async def _audit_strike(self, guild: discord.Guild, audit_action, action_name: str, delay: float = 0.0, obj=None):
        if not self._an_enabled(guild.id): return
        if not guild.me.guild_permissions.view_audit_log: return
        if delay: await asyncio.sleep(delay)

        for attempt in range(4):
            try:
                found_valid_entry = False
                async for entry in guild.audit_logs(limit=5, action=audit_action):
                    
                    # Ensure log is recent
                    age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                    if age > 15: continue

                    # CRITICAL FIX 1: Make sure the target of the audit log matches our object.
                    # This prevents old 'bot_add' events from spoofing the current 'bot_add' event.
                    if obj is not None and hasattr(entry, 'target') and entry.target:
                        if getattr(obj, 'id', None) != entry.target.id:
                            continue

                    # CRITICAL FIX 2: Deduplication skip (do not return, just continue searching)
                    dedup_key = (guild.id, action_name, entry.id)
                    if dedup_key in self._seen_entries_set:
                        continue 

                    found_valid_entry = True

                    if len(self._seen_entries) == self._seen_entries.maxlen:
                        evicted = self._seen_entries[0]
                        self._seen_entries_set.discard(evicted)
                    self._seen_entries.append(dedup_key)
                    self._seen_entries_set.add(dedup_key)

                    if not entry.user or entry.user.id == self.bot.user.id:
                        return 

                    actor = guild.get_member(entry.user.id)
                    if actor is None:
                        try: actor = await guild.fetch_member(entry.user.id)
                        except (discord.NotFound, discord.HTTPException): actor = entry.user

                    await self._register_attempt(guild, actor, action_name, obj=obj)
                    return 

                # If we exit the async loop without finding a NEW entry, we retry
                if not found_valid_entry and attempt < 3:
                    wait = 1.0 * (attempt + 1)
                    await asyncio.sleep(wait)
                    continue

                return
            except (discord.Forbidden, discord.HTTPException):
                return

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self._audit_strike(guild, discord.AuditLogAction.ban, "ban", delay=1.0, obj=user)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if not self._an_enabled(guild.id): return
        if not guild.me.guild_permissions.view_audit_log: return
        
        await asyncio.sleep(1.0)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                if entry.target and entry.target.id != member.id: continue
                if (discord.utils.utcnow() - entry.created_at).total_seconds() > 10: continue
                if not entry.user or entry.user.id == self.bot.user.id: return

                dedup_key = (guild.id, "kick", entry.id)
                if dedup_key in self._seen_entries_set: continue

                if len(self._seen_entries) == self._seen_entries.maxlen:
                    evicted = self._seen_entries[0]
                    self._seen_entries_set.discard(evicted)
                self._seen_entries.append(dedup_key)
                self._seen_entries_set.add(dedup_key)

                actor = guild.get_member(entry.user.id)
                if actor is None:
                    try: actor = await guild.fetch_member(entry.user.id)
                    except (discord.NotFound, discord.HTTPException): continue
                await self._register_attempt(guild, actor, "kick", obj=member)
                return
        except (discord.Forbidden, discord.HTTPException): pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if channel.name.startswith("ticket-") or channel.name.startswith("claimed-"):
            return
        await self._audit_strike(channel.guild, discord.AuditLogAction.channel_delete, "channel_delete", obj=channel)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if channel.name.startswith("ticket-") or channel.name.startswith("claimed-"):
            return
        await self._audit_strike(channel.guild, discord.AuditLogAction.channel_create, "channel_create", obj=channel)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if role.managed: return # Ignore bot integration roles
        await self._audit_strike(role.guild, discord.AuditLogAction.role_delete, "role_delete", obj=role)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        # CRITICAL FIX 3: Ignore bot integration roles! This is what triggered your false DM!
        if role.managed: return 
        await self._audit_strike(role.guild, discord.AuditLogAction.role_create, "role_create", obj=role)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        await self._audit_strike(after, discord.AuditLogAction.guild_update, "guild_update", obj=after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member.bot: return
        guild = member.guild

        # When a bot (re-)joins, purge any stale in-memory action counts for it.
        # Without this, a bot banned and later unbanned would carry its old counts
        # and could trip the threshold instantly after being re-invited.
        for _action_bucket in self._action_log.get(guild.id, {}).values():
            _action_bucket.pop(member.id, None)

        if not self._an_enabled(guild.id): return

        # Find who invited this bot via audit log
        await asyncio.sleep(1.0)
        inviter = None
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                if entry.target and entry.target.id == member.id:
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() <= 15:
                        inviter = guild.get_member(entry.user.id)
                        if inviter is None:
                            try: inviter = await guild.fetch_member(entry.user.id)
                            except (discord.NotFound, discord.HTTPException): inviter = entry.user
                        break
        except (discord.Forbidden, discord.HTTPException):
            pass

        # If we couldn't find the inviter, fall back to generic audit_strike
        if inviter is None:
            await self._audit_strike(guild, discord.AuditLogAction.bot_add, "bot_add", obj=member)
            return

        # If inviter is protected (owner / hardcoded owner / whitelisted),
        # check how many bot_adds they've done in the window.
        # If still within threshold → allow the bot, do nothing.
        # If they somehow blew past the threshold → still ban the bot but spare the inviter.
        if self._is_protected(guild.id, inviter.id, scope="antinuke"):
            count = self._record_action(guild.id, "bot_add", inviter.id)
            threshold = self._an_threshold(guild.id, "bot_add")
            if count < threshold:
                # Fully within limit and authorised — allow bot
                return
            # Over threshold even for an authorised user: ban the bot but don't strip them
            self._pending_obj.setdefault(guild.id, {}).setdefault("bot_add", []).append(member)
            await self._an_punish(guild, inviter, "bot_add", count)
            return

        # Unauthorised inviter — go through normal threshold logic
        await self._register_attempt(guild, inviter, "bot_add", obj=member)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        await self._audit_strike(channel.guild, discord.AuditLogAction.webhook_create, "webhook", obj=channel)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after):
        if len(before) > len(after):
            deleted_count = len(before) - len(after)
            for _ in range(deleted_count):
                await self._audit_strike(guild, discord.AuditLogAction.emoji_delete, "emoji_delete")
        elif len(after) > len(before):
            created_count = len(after) - len(before)
            for _ in range(created_count):
                await self._audit_strike(guild, discord.AuditLogAction.emoji_create, "emoji_create")

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild: discord.Guild, before, after):
        if len(before) > len(after):
            deleted_count = len(before) - len(after)
            for _ in range(deleted_count):
                await self._audit_strike(guild, discord.AuditLogAction.sticker_delete, "sticker_delete")
        elif len(after) > len(before):
            created_count = len(after) - len(before)
            for _ in range(created_count):
                await self._audit_strike(guild, discord.AuditLogAction.sticker_create, "sticker_create")

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if entry.action == discord.AuditLogAction.member_prune:
            actor = entry.guild.get_member(entry.user.id)
            if not actor: return
            await self._register_attempt(entry.guild, actor, "member_prune")

    # ==========================================================================
    #  LAYER 2 -- AUTOMOD
    # ==========================================================================

    def _am_cfg(self, guild_id: int) -> dict:
        key    = str(guild_id)
        is_new = key not in self._am_data
        cfg    = _automod_cfg(self._am_data, guild_id)
        if is_new:
            _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
        return cfg

    def _am_enabled(self, guild_id: int) -> bool:
        return _automod_cfg(self._am_data, guild_id).get("enabled", False)

    def _get_strike_count(self, guild_id: int, user_id: int) -> int:
        now    = time.time()
        expire = _automod_cfg(self._am_data, guild_id)["warn_expire"]
        entry  = self._am_strikes[guild_id].get(user_id)
        if entry is None: return 0
        if now - entry.get("last", 0) >= expire:
            del self._am_strikes[guild_id][user_id]
            self._persist_strikes()
            return 0
        return entry.get("strikes", 0)

    def _am_add_strike(self, guild_id: int, user_id: int) -> int:
        current   = self._get_strike_count(guild_id, user_id)
        new_count = current + 1
        self._am_strikes[guild_id][user_id] = {"strikes": new_count, "last": time.time()}
        self._persist_strikes()
        return new_count

    def _am_reset_strikes(self, guild_id: int, user_id: int):
        if user_id in self._am_strikes[guild_id]:
            del self._am_strikes[guild_id][user_id]
            self._persist_strikes()

    def _is_spam(self, guild_id: int, user_id: int, cfg: dict) -> bool:
        now    = time.time()
        window = cfg["spam_window"]
        q      = self._msg_times[guild_id][user_id]
        q.append(now)
        while q and now - q[0] > window:
            q.popleft()
        return len(q) >= cfg["spam_count"]

    async def _am_punish(self, guild: discord.Guild, member: discord.Member, strike: int, cfg: dict, channel: discord.TextChannel = None, trigger: str = "spam"):
        action_taken  = ""
        timeout_until = None

        if strike == 1:
            action_taken = "warn"
            try: await member.send(embed=self._am_warn_dm_embed(guild, cfg))
            except discord.Forbidden: pass
        elif strike == 2:
            action_taken  = "timeout"
            timeout_until = _now_ts() + datetime.timedelta(seconds=cfg["timeout_s"])
            try: await member.timeout(timeout_until, reason=f"[Paladin AutoMod] {trigger.title()} -- Strike 2")
            except discord.Forbidden: action_taken = "timeout (failed -- missing permissions)"
            except Exception as e: action_taken = f"timeout (error: {e})"
        elif strike == 3:
            action_taken = "kick"
            try:
                await member.kick(reason=f"[Paladin AutoMod] {trigger.title()} -- Strike 3")
            except discord.Forbidden:
                action_taken = "kick (failed -- missing permissions)"
            except Exception as e:
                action_taken = f"kick (error: {e})"
        else:
            action_taken = "ban"
            try:
                await member.ban(reason=f"[Paladin AutoMod] {trigger.title()} -- Strike {strike}")
            except discord.Forbidden:
                action_taken = "ban (failed -- missing permissions)"
            except Exception as e:
                action_taken = f"ban (error: {e})"

        if channel and channel.permissions_for(guild.me).send_messages:
            try: await channel.send(embed=self._am_channel_embed(member, strike, action_taken, timeout_until, cfg, trigger=trigger))
            except (discord.Forbidden, discord.HTTPException): pass

        await self._log(guild, self._am_log_embed(member, strike, action_taken, timeout_until, cfg, trigger=trigger))

        alert_embed = self._alertnuke_automod_embed(guild, member, strike, action_taken, trigger)
        await self._alertnuke_dm(guild, alert_embed)

    def _am_warn_dm_embed(self, guild: discord.Guild, cfg: dict) -> discord.Embed:
        embed = _base_embed(
            f"{ICO_WARN}  Automated Warning  --  {guild.name}",
            COL_WARNING,
        )
        embed.description = (
            "You have been **automatically warned** for sending messages too quickly.\n"
            "Please slow down -- further action will be taken if this continues."
        )
        embed.add_field(name="Current Strike", value="`1 of 4`", inline=True)
        embed.add_field(name="Expires In", value=f"`{_fmt_dur(cfg['warn_expire'])}`", inline=True)
        embed.add_field(name="What happens next", value="Strike 2 -> Timeout\nStrike 3 -> Kick\nStrike 4 -> Ban", inline=False)
        embed.set_footer(text=f"Paladin AutoMod  |  {guild.name}")
        return embed

    def _am_channel_embed(self, member: discord.Member, strike: int, action: str, timeout_until, cfg: dict, trigger: str = "spam") -> discord.Embed:
        base_action = action.split()[0]
        colours = {"warn": COL_WARNING, "timeout": COL_WARNING, "kick": COL_DANGER, "ban": COL_CRITICAL}
        icons   = {"warn": "⚠️",        "timeout": "⏱️",        "kick": "👢",       "ban": "🔨"}

        colour = colours.get(base_action, COL_DANGER)
        icon   = icons.get(base_action, "🤖")
        filled = min(strike, 4)
        bar    = "".join("[x]" if i < filled else "[ ]" for i in range(4))

        titles = {
            "warn":    f"⚠️  {member.display_name}, please stop.",
            "timeout": f"⏱️  {member.display_name} has been timed out.",
            "kick":    f"👢  {member.display_name} has been kicked.",
            "ban":     f"🔨  {member.display_name} has been banned.",
        }
        descriptions = {
            "warn": f"{member.mention} — you have been flagged for **{trigger.replace('_', ' ')}**.\n**This is your first warning.** Further violations will result in a timeout, kick, or ban.",
            "timeout": f"{member.mention} has been **timed out** for {trigger.replace('_', ' ')}.\n" + (f"Timeout expires {discord.utils.format_dt(timeout_until, 'R')}." if timeout_until else ""),
            "kick": f"{member.mention} has been **kicked** from the server for repeated violations.",
            "ban": f"{member.mention} has been **permanently banned** for repeated violations (Strike {strike}).",
        }

        embed = _base_embed(title=titles.get(base_action, f"{icon}  AutoMod action taken"), colour=colour, description=descriptions.get(base_action, f"Action taken: `{action}`"))
        embed.add_field(name="Strike Progress", value=f"`{bar}`  `{strike}/4`", inline=True)
        embed.add_field(name="Strikes Reset", value=discord.utils.format_dt(_now_ts() + datetime.timedelta(seconds=cfg["warn_expire"]), "R"), inline=True)
        embed.add_field(name="Ladder", value="1 → Warn  |  2 → Timeout  |  3 → Kick  |  4 → Ban", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Paladin AutoMod  ◈  MonkeyBytes")
        return embed

    def _am_log_embed(self, member: discord.Member, strike: int, action: str, timeout_until, cfg: dict, trigger: str = "spam") -> discord.Embed:
        base_action = action.split()[0]
        colours = {"warn": COL_WARNING, "timeout": COL_WARNING, "kick": COL_DANGER, "ban": COL_CRITICAL}
        icons   = {"warn": "⚠️",        "timeout": "⏱️",        "kick": "👢",       "ban": "🔨"}
        labels  = {"warn": "Warned",    "timeout": "Timed Out", "kick": "Kicked",   "ban": "Banned"}

        colour = colours.get(base_action, COL_DANGER)
        icon   = icons.get(base_action, "🤖")
        label  = labels.get(base_action, action.title())
        filled = min(strike, 4)
        bar = "".join("[x]" if i < filled else "[ ]" for i in range(4))

        embed = _base_embed(f"{ICO_SHIELD}  AutoMod -- {icon} {label}", colour)
        embed.set_author(name=f"{member} ({member.id})", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = f"> Strike progress: `{bar}`"

        embed.add_field(name="Member",  value=f"{member.mention}\n`{member}`", inline=True)
        embed.add_field(name="Action",  value=f"`{action}`",                   inline=True)
        embed.add_field(name="Strike",  value=f"`{strike} / 4`",               inline=True)
        embed.add_field(name="Trigger", value=f"`{trigger.replace('_', ' ').title()}`", inline=True)
        embed.add_field(name="Strikes Expire", value=discord.utils.format_dt(_now_ts() + datetime.timedelta(seconds=cfg["warn_expire"]), "R"), inline=True)

        if timeout_until:
            embed.add_field(name="Timeout Until", value=discord.utils.format_dt(timeout_until, "R"), inline=True)

        embed.set_footer(text=f"Paladin AutoMod  |  Guild: {member.guild.name}")
        return embed

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot: return
        member = message.author
        if not isinstance(member, discord.Member): return
        guild = message.guild

        if not self._am_enabled(guild.id): return
        if self._is_protected(guild.id, member.id, scope="automod"): return
        cfg = _automod_cfg(self._am_data, guild.id)

        # ---- Fix 1: hold the lock only for in-memory state; release before I/O ----
        # Acquiring the lock for the full execution (including network calls like
        # _am_punish) meant that any rate-limit stall kept the lock held, blocking
        # every subsequent message from this user and causing cascading false strikes.
        # Instead we determine the trigger and increment the strike counter under the
        # lock, then release it before performing any Discord API calls.
        trigger   = None
        strike    = None

        async with self._get_am_lock(guild.id, member.id):
            banned = cfg.get("banned_words", [])
            if banned:
                lower = message.content.lower()
                alpha_words  = [w for w in banned if re.search(r'\w', w)]
                symbol_words = [w for w in banned if not re.search(r'\w', w)]
                matched = False
                if alpha_words:
                    pattern = self._get_banned_pattern(guild.id, cfg)
                    if pattern and re.search(pattern, lower):
                        matched = True
                if not matched and symbol_words:
                    # FIX #2: Use word boundaries for symbol matching to prevent single-char punctuation
                    # from matching every message. For example, "." should not match in "word.com"
                    for symbol in symbol_words:
                        # Escape special regex characters
                        escaped = re.escape(symbol)
                        # Pattern: symbol must NOT be adjacent to alphanumerics
                        # This prevents "." from matching every period, but allows "!!!" to match
                        pattern = rf'(?<![a-zA-Z0-9]){escaped}(?![a-zA-Z0-9])'
                        if re.search(pattern, lower):
                            matched = True
                            break
                if matched:
                    strike  = self._am_add_strike(guild.id, member.id)
                    trigger = "banned_word"

            if trigger is None and cfg.get("anti_invite", False):
                if re.search(r"discord(?:app)?\.(?:gg|com)/(?:invite/)?[\w-]+", message.content, re.IGNORECASE):
                    strike  = self._am_add_strike(guild.id, member.id)
                    trigger = "invite_link"

            if trigger is None:
                limit = cfg.get("mentions_limit", 5)
                if limit > 0:
                    raw_user_mentions = len(re.findall(r"<@!?\d+>", message.content))
                    raw_role_mentions = len(re.findall(r"<@&\d+>", message.content))
                    # @everyone / @here do not produce <@id> patterns so we add 1
                    # separately.  We do NOT use len(message.mentions) here because
                    # that is the same set already counted by raw_user_mentions — using
                    # it again would double-count every user ping.
                    everyone_count = 1 if message.mention_everyone else 0
                    if raw_user_mentions + raw_role_mentions + everyone_count >= limit:
                        strike  = self._am_add_strike(guild.id, member.id)
                        trigger = "mass_mention"

            if trigger is None:
                if not self._is_spam(guild.id, member.id, cfg):
                    return
                self._msg_times[guild.id][member.id].clear()
                strike  = self._am_add_strike(guild.id, member.id)
                trigger = "message_spam"

        # Lock released — safe to do network I/O now
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        await self._am_punish(guild, member, strike, cfg, channel=message.channel, trigger=trigger)

    # ==========================================================================
    #  WHITELIST COMMANDS
    # ==========================================================================

    def _wl_list(self, guild_id: int) -> list:
        return self._wl.get(str(guild_id), [])

    def _wl_toggle(self, guild_id: int, user_id: int) -> bool:
        key = str(guild_id)
        self._wl.setdefault(key, [])
        if user_id in self._wl[key]:
            self._wl[key].remove(user_id)
            _save(self._wl, WHITELIST_FILE, self._save_lock)
            return False
        self._wl[key].append(user_id)
        _save(self._wl, WHITELIST_FILE, self._save_lock)
        return True

    @commands.command(name="whitelist", description="Toggle a user on/off the Paladin whitelist.")
    @commands.guild_only()
    async def whitelist(self, ctx: commands.Context, user: discord.Member):
        if not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can manage the whitelist."), ephemeral=True)

        added = self._wl_toggle(ctx.guild.id, user.id)

        if added:
            embed = _base_embed(f"{ICO_SHIELD}  Paladin Whitelist -- User Added", COL_SUCCESS)
            embed.description = f"{ICO_SUCCESS} {user.mention} has been **added** to the whitelist."
            embed.add_field(name="Effect", value="Bypasses both Antinuke and AutoMod", inline=False)
        else:
            embed = _base_embed(f"{ICO_SHIELD}  Paladin Whitelist -- User Removed", COL_MUTED)
            embed.description = f"**{user}** has been **removed** from the whitelist."

        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"Managed by {ctx.author}  |  User ID: {user.id}")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name="whitelistshow", description="Show the Paladin whitelist for this server.")
    @commands.guild_only()
    async def whitelistshow(self, ctx: commands.Context):
        if not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can view the whitelist."), ephemeral=True)

        wl = self._wl_list(ctx.guild.id)

        if not wl:
            embed = _base_embed(f"{ICO_SHIELD}  Paladin Whitelist", COL_BRAND)
            embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
            embed.description = "*No users are currently whitelisted.*"
            embed.set_footer(text="0 user(s) whitelisted  |  Hardcoded owners & server owner always exempt")
            return await ctx.send(embed=embed, ephemeral=True)

        lines = []
        for uid in wl:
            m = ctx.guild.get_member(uid)
            lines.append(f"` {'>' if m else '?'} ` {m.mention if m else f'<@{uid}>'} -- `{uid}`")

        pages, current, current_len = [], [], 0
        for line in lines:
            if current_len + len(line) + 1 > 3900:
                pages.append("\n".join(current))
                current, current_len = [], 0
            current.append(line)
            current_len += len(line) + 1
        if current:
            pages.append("\n".join(current))

        total_pages = len(pages)
        for i, page_text in enumerate(pages, 1):
            embed = _base_embed(f"{ICO_SHIELD}  Paladin Whitelist", COL_BRAND)
            embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
            embed.description = page_text
            footer = f"{len(wl)} user(s) whitelisted  |  Hardcoded owners & server owner always exempt"
            if total_pages > 1:
                footer += f"  |  Page {i}/{total_pages}"
            embed.set_footer(text=footer)
            await ctx.send(embed=embed, ephemeral=True)

    # ==========================================================================
    #  ANTINUKE COMMANDS
    # ==========================================================================

    @commands.command(name="paladinstart", description="Toggle Paladin Antinuke protection on/off.")
    @commands.guild_only()
    async def paladinstart(self, ctx: commands.Context):
        if not self._can_manage(ctx.author, ctx.guild): return

        cfg     = self._an_cfg(ctx.guild.id)
        cfg["enabled"] = not cfg["enabled"]
        _save(self._an_data, DATA_FILE, self._save_lock)
        state   = cfg["enabled"]

        embed = _base_embed(
            f"{ICO_SHIELD}  Antinuke -- {'Enabled' if state else 'Disabled'}",
            COL_SUCCESS if state else COL_MUTED,
        )
        embed.description = _status_bar(state)
        embed.add_field(name="Window", value=f"`{_fmt_dur(cfg['window'])}`", inline=True)
        embed.add_field(name="Decay", value=f"`{_fmt_dur(cfg['decay'])}`", inline=True)
        embed.set_footer(text=f"Changed by {ctx.author}  |  Paladin Antinuke")
        await ctx.send(embed=embed)

    @commands.command(name="paladinset", description="Configure Paladin Antinuke settings.")
    @commands.guild_only()
    @app_commands.describe(setting="Setting: threshold / decay / window (blank to view)", args="Arguments: for threshold use 'ban 2', for decay/window use '30m'")
    async def paladinset(self, ctx: commands.Context, setting: str = None, *, args: str = None):
        if not self._can_manage(ctx.author, ctx.guild): return
        cfg = self._an_cfg(ctx.guild.id)

        if setting is None:
            embed = _base_embed(f"{ICO_SHIELD}  Antinuke -- Configuration", COL_BRAND)
            embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
            embed.description = _status_bar(cfg["enabled"])
            embed.add_field(name="Rolling Window", value=f"`{_fmt_dur(cfg['window'])}`", inline=True)
            embed.add_field(name="Strike Decay",   value=f"`{_fmt_dur(cfg['decay'])}`",  inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)

            threshold_lines = "\n".join(f"`{v:>2}` -- {_action_label(k)}" for k, v in cfg["thresholds"].items())
            embed.add_field(name="Action Thresholds", value=threshold_lines, inline=True)

            wl     = self._wl_list(ctx.guild.id)
            wl_fmt = ", ".join(f"<@{uid}>" for uid in wl) if wl else "*None*"
            embed.add_field(name="Whitelist", value=wl_fmt, inline=True)
            embed.set_footer(text="!paladinstart to toggle  |  !paladinset threshold <action> <n>")
            return await ctx.send(embed=embed)

        setting = setting.lower()
        _args = args.split() if args else []

        if setting == "threshold":
            if len(_args) < 2:
                actions_fmt = "  ".join(f"`{a}`" for a in DEFAULT_ANTINUKE_THRESHOLDS)
                return await ctx.send(embed=_err(f"Usage: `!paladinset threshold <action> <n>`\nValid actions: {actions_fmt}"))
            action, value = _args[0].lower(), _args[1]
            if action not in DEFAULT_ANTINUKE_THRESHOLDS:
                return await ctx.send(embed=_err(f"Unknown action `{action}`.\nValid: {', '.join(DEFAULT_ANTINUKE_THRESHOLDS)}"))
            try:
                n = int(value)
                assert n >= 1
            except (ValueError, AssertionError):
                return await ctx.send(embed=_err("Threshold must be a whole number 1 or higher."))

            cfg["thresholds"][action] = n
            _save(self._an_data, DATA_FILE, self._save_lock)

            embed = _base_embed(f"{ICO_SHIELD}  Antinuke -- Threshold Updated", COL_SUCCESS)
            embed.add_field(name="Action",    value=_action_label(action),    inline=True)
            embed.add_field(name="New Value", value=f"`{n}` per window",       inline=True)
            embed.add_field(name="Window",    value=f"`{_fmt_dur(cfg['window'])}`", inline=True)
            embed.set_footer(text=f"Changed by {ctx.author}")
            return await ctx.send(embed=embed)

        if setting == "decay":
            if not _args: return await ctx.send(embed=_err("Usage: `!paladinset decay <duration>`  e.g. `30m` `1h`"))
            secs = _parse_duration(_args[0])
            if secs is None: return await ctx.send(embed=_err("Invalid duration. Examples: `30m`, `1h`, `10s`"))
            cfg["decay"] = secs
            _save(self._an_data, DATA_FILE, self._save_lock)
            embed = _ok(f"Strike decay window set to `{_fmt_dur(secs)}`.")
            embed.set_footer(text=f"Changed by {ctx.author}")
            return await ctx.send(embed=embed)

        if setting == "window":
            if not _args: return await ctx.send(embed=_err("Usage: `!paladinset window <duration>`  e.g. `60s` `2m`"))
            secs = _parse_duration(_args[0])
            if secs is None: return await ctx.send(embed=_err("Invalid duration. Examples: `60s`, `2m`"))
            cfg["window"] = secs
            _save(self._an_data, DATA_FILE, self._save_lock)
            embed = _ok(f"Rolling action window set to `{_fmt_dur(secs)}`.")
            embed.set_footer(text=f"Changed by {ctx.author}")
            return await ctx.send(embed=embed)

        await ctx.send(embed=_err("Unknown setting. Valid options: `threshold`, `decay`, `window`.\nRun `!paladinset` with no arguments to see all current settings."))

    @commands.command(name="paladinreset", description="Reset all antinuke action counters for the server.")
    @commands.guild_only()
    async def paladinreset(self, ctx: commands.Context):
        if not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can use this."), ephemeral=True)

        if ctx.guild.id in self._action_log:
            self._action_log[ctx.guild.id].clear()
        if ctx.guild.id in self._pending_obj:
            self._pending_obj[ctx.guild.id].clear()

        embed = _base_embed(f"{ICO_SUCCESS}  Antinuke Counters Reset", COL_SUCCESS)
        embed.description = "All antinuke action counters and pending reversions have been completely cleared for this server."
        embed.set_footer(text=f"Reset by {ctx.author}")
        await ctx.send(embed=embed)

    # ==========================================================================
    #  AUTOMOD COMMANDS
    # ==========================================================================

    @commands.command(name="automodstart", description="Toggle Paladin AutoMod spam protection on/off.")
    @commands.guild_only()
    async def automodstart(self, ctx: commands.Context):
        if not self._can_manage(ctx.author, ctx.guild): return

        cfg            = self._am_cfg(ctx.guild.id)
        cfg["enabled"] = not cfg["enabled"]
        _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
        state = cfg["enabled"]

        embed = _base_embed(f"{ICO_SHIELD}  AutoMod -- {'Enabled' if state else 'Disabled'}", COL_SUCCESS if state else COL_MUTED)
        embed.description = _status_bar(state)
        embed.add_field(name="Spam Trigger", value=f"`{cfg['spam_count']}` msgs / `{cfg['spam_window']}s`", inline=True)
        embed.add_field(name="Warn Expires", value=f"`{_fmt_dur(cfg['warn_expire'])}`", inline=True)
        embed.set_footer(text=f"Changed by {ctx.author}  |  Paladin AutoMod")
        await ctx.send(embed=embed)

    @commands.command(name="automodset", description="Configure Paladin AutoMod settings.")
    @commands.guild_only()
    async def automodset(self, ctx: commands.Context, setting: str = None, *, value: str = None):
        if not self._can_manage(ctx.author, ctx.guild): return
        cfg = self._am_cfg(ctx.guild.id)

        if setting is None:
            embed = _base_embed(f"{ICO_SHIELD}  AutoMod -- Configuration", COL_WARNING)
            embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
            embed.description = _status_bar(cfg["enabled"])

            embed.add_field(name="Spam Count",   value=f"`{cfg['spam_count']}` messages",        inline=True)
            embed.add_field(name="Spam Window",  value=f"`{cfg['spam_window']}s`",                inline=True)
            embed.add_field(name="Warn Expires", value=f"`{_fmt_dur(cfg['warn_expire'])}`",       inline=True)
            embed.add_field(name="Timeout",      value=f"`{_fmt_dur(cfg['timeout_s'])}`",         inline=True)
            embed.add_field(name="Mentions Limit", value=f"`{cfg['mentions_limit']}`" if cfg.get('mentions_limit') else "Off", inline=True)
            embed.add_field(name="Anti-Invite",  value="🟢 On" if cfg.get("anti_invite") else "⚫ Off", inline=True)

            banned = cfg.get("banned_words", [])
            banned_str = f"`{len(banned)} word(s) filtered`" if banned else "*None*"
            embed.add_field(name="Word Filter", value=banned_str, inline=False)

            embed.add_field(
                name="Punishment Ladder",
                value="```\nStrike 1  ->  Warn (DM)\n" f"Strike 2  ->  Timeout [{_fmt_dur(cfg['timeout_s'])}]\n" "Strike 3  ->  Kick\nStrike 4  ->  Ban\n```",
                inline=False,
            )
            embed.set_footer(text="!automodstart to toggle  |  !automodset <setting> <value>")
            return await ctx.send(embed=embed)

        setting = setting.lower()

        if setting == "spam_count":
            try:
                n = int(value)
                assert n >= 2
            except (ValueError, TypeError, AssertionError): return await ctx.send(embed=_err("Usage: `!automodset spam_count <n>`  (minimum 2)"))
            cfg["spam_count"] = n
            _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            embed = _ok(f"Spam trigger set to `{n}` messages in window.")
            embed.set_footer(text=f"Changed by {ctx.author}")
            return await ctx.send(embed=embed)

        if setting == "spam_window":
            try:
                secs = int(value)
                assert 1 <= secs <= 60
            except (ValueError, TypeError, AssertionError): return await ctx.send(embed=_err("Usage: `!automodset spam_window <seconds>`  (1-60)"))
            cfg["spam_window"] = secs
            _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            embed = _ok(f"Spam detection window set to `{secs}s`.")
            embed.set_footer(text=f"Changed by {ctx.author}")
            return await ctx.send(embed=embed)

        if setting == "timeout":
            secs = _parse_duration(value or "")
            if secs is None: return await ctx.send(embed=_err("Usage: `!automodset timeout <duration>`  e.g. `10m` `1h`"))
            cfg["timeout_s"] = secs
            _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            embed = _ok(f"Strike 2 timeout set to `{_fmt_dur(secs)}`.")
            embed.set_footer(text=f"Changed by {ctx.author}")
            return await ctx.send(embed=embed)

        if setting == "warn_expire":
            secs = _parse_duration(value or "")
            if secs is None: return await ctx.send(embed=_err("Usage: `!automodset warn_expire <duration>`  e.g. `6h` `12h` `1d`"))
            cfg["warn_expire"] = secs
            _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            embed = _ok(f"Strike expiry set to `{_fmt_dur(secs)}`.")
            embed.set_footer(text=f"Changed by {ctx.author}")
            return await ctx.send(embed=embed)

        if setting == "mentions":
            try:
                n = int(value)
                assert n >= 0
            except (ValueError, AssertionError): return await ctx.send(embed=_err("Usage: `!automodset mentions <n>` (0 to disable)."))
            cfg["mentions_limit"] = n
            _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            return await ctx.send(embed=_ok(f"Mentions limit set to `{n}` (0 is off)."))

        if setting == "invites":
            if not value: return await ctx.send(embed=_err("Usage: `!automodset invites <on/off>`."))
            if value.lower() in ("on", "yes", "true", "1"): cfg["anti_invite"] = True
            elif value.lower() in ("off", "no", "false", "0"): cfg["anti_invite"] = False
            else: return await ctx.send(embed=_err("Usage: `!automodset invites <on/off>`."))
            _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            return await ctx.send(embed=_ok(f"Anti-Invite is now `{'ON' if cfg['anti_invite'] else 'OFF'}`."))

        if setting == "filter_add":
            if not value: return await ctx.send(embed=_err("Provide a word to filter."))
            word = value.lower().strip()
            
            # FIX #2: Validate the word to prevent mass-bans from single punctuation
            is_alphanumeric = bool(re.search(r'\w', word))
            
            if len(word) < 2:
                return await ctx.send(embed=_err("Banned words must be at least 2 characters."))
            
            if not is_alphanumeric and len(word) < 3:
                return await ctx.send(embed=_err("Symbol-only filters must be at least 3 characters (e.g., `!!!` instead of `!`). This prevents accidental mass-bans."))
            
            if word not in cfg["banned_words"]:
                cfg["banned_words"].append(word)
                _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            return await ctx.send(embed=_ok(f"Added `{word}` to the word filter."))

        if setting == "filter_remove":
            if not value: return await ctx.send(embed=_err("Provide a word to remove."))
            word = value.lower()
            if word in cfg["banned_words"]:
                cfg["banned_words"].remove(word)
                _save(self._am_data, AUTOMOD_DATA_FILE, self._save_lock)
            return await ctx.send(embed=_ok(f"Removed `{word}` from the word filter."))

        await ctx.send(embed=_err("Unknown setting.\nValid: `spam_count`, `spam_window`, `timeout`, `warn_expire`, `mentions`, `invites`, `filter_add`, `filter_remove`.\nRun `!automodset` with no arguments to see all current settings."))

    @commands.command(name="automodclear", description="Clear all automod strikes for a specific user.")
    @commands.guild_only()
    async def automodclear(self, ctx: commands.Context, member: discord.Member):
        if not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can use this."), ephemeral=True)

        self._am_reset_strikes(ctx.guild.id, member.id)

        embed = _base_embed(f"{ICO_SUCCESS}  AutoMod Strikes Cleared", COL_SUCCESS)
        embed.description = f"All automod strikes and expiry trackers have been completely reset for {member.mention}."
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Cleared by {ctx.author}")
        await ctx.send(embed=embed)

    # ==========================================================================
    #  CUSTOM HELP PAGE
    # ==========================================================================

    def help_embed(self, prefix: str = "!") -> discord.Embed:
        embed = _base_embed(f"{ICO_SHIELD}  Paladin Protection System", COL_BRAND)
        embed.description = "Two-layer server security built into one system.\n**Antinuke** guards against malicious moderators. **AutoMod** handles member spam progressively.\n\n*Only the server owner and bot owners can configure these settings.*"

        embed.add_field(name=f"{'='*16}  {ICO_ANTINUKE}  ANTINUKE  {'='*16}", value="Strips all roles from any mod who performs destructive actions beyond the threshold.\nFor bot adds: **the bot is banned** and the inviter's roles are stripped.\nTriggers **before** any confirm button -- and catches right-click actions too.", inline=False)
        embed.add_field(name=f"`{prefix}paladinstart`  /  `{prefix}paladinset`", value=f"Toggle Antinuke on/off.  |  View all settings.", inline=False)
        embed.add_field(name=f"`{prefix}paladinset threshold <action> <n>`", value=f"Set the strike threshold for a specific action.\nActions: `ban` `kick` `channel_delete` `channel_create` `role_delete` `role_create` `guild_update` `bot_add` `webhook` `emoji_delete` `emoji_create` `sticker_delete` `sticker_create` `member_prune`", inline=False)
        embed.add_field(name=f"`{prefix}paladinset window <duration>`", value=f"Rolling window to count actions in.", inline=True)
        embed.add_field(name=f"`{prefix}paladinset decay <duration>`", value=f"How long strike memory lasts.", inline=True)
        embed.add_field(name=f"`{prefix}paladinreset`", value=f"Reset all active antinuke counters.", inline=True)

        embed.add_field(
            name=f"{'='*16}  {ICO_AUTOMOD}  AUTOMOD  {'='*16}",
            value="Detects message spam from regular members and applies a progressive ladder:\n"
                  "```\nStrike 1  ->  Warn (DM)\nStrike 2  ->  Timeout\n"
                  "Strike 3  ->  Kick\nStrike 4  ->  Ban\n```"
                  "**Protected from AutoMod:** Server owner, hardcoded bot owners, and explicitly whitelisted users.\n"
                  "❗ Other moderators must be explicitly whitelisted to bypass AutoMod.",
            inline=False
        )
        embed.add_field(name=f"`{prefix}automodstart`  /  `{prefix}automodset`", value=f"Toggle AutoMod on/off.  |  View all settings.", inline=False)
        embed.add_field(name=f"`{prefix}automodset spam_count <n>`", value="Messages in window before triggering.", inline=True)
        embed.add_field(name=f"`{prefix}automodset spam_window <s>`", value="Detection window in seconds (1-60).", inline=True)
        embed.add_field(name=f"`{prefix}automodset timeout <duration>`", value="Strike 2 timeout length.", inline=True)
        embed.add_field(name=f"`{prefix}automodset warn_expire <dur>`", value="How long before strikes reset.", inline=True)
        embed.add_field(name=f"`{prefix}automodset mentions <n>`", value="Max mentions per message.", inline=True)
        embed.add_field(name=f"`{prefix}automodset invites <on/off>`", value="Anti-Discord Invites.", inline=True)
        embed.add_field(name="Filter Words", value=f"`{prefix}automodset filter_add <word>`\n`{prefix}automodset filter_remove <word>`", inline=True)
        embed.add_field(name=f"`{prefix}automodclear <@user>`", value="Wipe a user's strike history entirely.", inline=True)

        embed.add_field(name=f"{'='*16}  {ICO_INFO}  UTILITIES  {'='*16}", value="Quick-access dashboard and lookup commands.", inline=False)
        embed.add_field(name=f"`{prefix}paladinstatus`  /  `{prefix}automodstrikes <@user>`", value=f"Full settings dashboard.  |  View a user's current automod strikes.", inline=False)

        embed.add_field(name=f"{'='*16}  {ICO_WL}  WHITELIST & ALERTS {'='*16}", value="Whitelists and DM alerting configurations.", inline=False)
        embed.add_field(name=f"`{prefix}whitelist <@user>` / `{prefix}whitelistshow`", value="Manage whitelist bypasses.", inline=True)
        embed.add_field(name=f"`{prefix}paladinalert <@user>` / `{prefix}alertnukelist`", value="Manage DM alert subscriptions.", inline=True)

        embed.add_field(name=f"{'='*16}  {ICO_KEY}  BYPASS KEYS {'='*16}", value="Temporary bypass key management for trusted users.", inline=False)
        embed.add_field(name=f"`{prefix}generatekey`", value="Generate a bypass key (options via DM).", inline=True)
        embed.add_field(name=f"`{prefix}usekey <key>`", value="Redeem a bypass key (message auto-deleted).", inline=True)

        embed.set_footer(text=f"{ICO_SHIELD} Paladin Protection  |  Use {prefix}cmd or /cmd")
        return embed

    @commands.command(name="paladinhelp", description="Show the full Paladin command reference.")
    @commands.guild_only()
    async def paladinhelp(self, ctx: commands.Context):
        prefix = ctx.prefix or "!"
        await ctx.send(embed=self.help_embed(prefix=prefix), ephemeral=True)

    @commands.command(name="paladinstatus", description="Show a full dashboard of all Paladin settings.")
    @commands.guild_only()
    async def paladinstatus(self, ctx):
        if not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can view this."), ephemeral=True)

        an = _antinuke_cfg(self._an_data, ctx.guild.id)
        am = _automod_cfg(self._am_data, ctx.guild.id)
        wl = self._wl.get(str(ctx.guild.id), [])

        an_status = "🟢 ACTIVE" if an.get("enabled") else "🔴 INACTIVE"
        thresholds = an.get("thresholds", DEFAULT_ANTINUKE_THRESHOLDS)
        threshold_lines = []
        for action, thresh in thresholds.items():
            meta = ACTION_META.get(action, (action, "❓"))
            threshold_lines.append(f"{meta[1]} {meta[0]}: `{thresh}`")
        threshold_str = "\n".join(threshold_lines)

        embed = _base_embed(f"{ICO_SHIELD}  Paladin Dashboard  --  {ctx.guild.name}", COL_BRAND)
        if ctx.guild.icon: embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(name=f"{ICO_ANTINUKE}  Antinuke  [{an_status}]", value=f"**Window:** `{_fmt_dur(an['window'])}`  |  **Decay:** `{_fmt_dur(an['decay'])}`\n**Thresholds:**\n{threshold_str}", inline=False)

        am_status = "🟢 ACTIVE" if am.get("enabled") else "🔴 INACTIVE"
        banned_w = am.get("banned_words", [])
        banned_count = f"`{len(banned_w)} word(s) filtered`" if banned_w else "`None`"

        embed.add_field(name=f"{ICO_AUTOMOD}  AutoMod  [{am_status}]", value=f"**Spam:** `{am['spam_count']}` msgs in `{am['spam_window']}s`\n**Timeout:** `{_fmt_dur(am['timeout_s'])}`  |  **Strike Expiry:** `{_fmt_dur(am['warn_expire'])}`\n**Mentions Limit:** `{am.get('mentions_limit', 5)}`\n**Anti-Invite:** `{'ON' if am.get('anti_invite') else 'OFF'}`\n**Word Filter:** {banned_count}", inline=False)

        if wl:
            wl_mentions = [f"<@{uid}>" for uid in wl[:15]]
            wl_str = ", ".join(wl_mentions)
            if len(wl) > 15: wl_str += f" ... and {len(wl) - 15} more"
        else: wl_str = "No users whitelisted."

        embed.add_field(name=f"{ICO_WL}  Whitelist ({len(wl)})", value=wl_str, inline=False)
        embed.set_footer(text=f"Paladin Protection  |  {ctx.guild.name}")
        await ctx.send(embed=embed)

    @commands.command(name="automodstrikes", description="View a user's current AutoMod strike count.")
    @commands.guild_only()
    async def automodstrikes(self, ctx, member: discord.Member):
        if not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can view this."), ephemeral=True)

        count = self._get_strike_count(ctx.guild.id, member.id)
        cfg = _automod_cfg(self._am_data, ctx.guild.id)
        filled = min(count, 4)
        bar = "".join("[x]" if i < filled else "[ ]" for i in range(4))

        if count == 0:
            colour = COL_SUCCESS
            desc = f"✅ **{member.display_name}** has **no active strikes**."
            expiry_str = "N/A"
        else:
            colour = COL_WARNING if count < 3 else COL_DANGER
            entry = self._am_strikes.get(ctx.guild.id, {}).get(member.id, {})
            last = entry.get("last", 0)
            expire_at = last + cfg["warn_expire"]
            expiry_str = f"<t:{int(expire_at)}:R>"
            desc = f"**{member.display_name}** has **{count}** active strike{'s' if count != 1 else ''}."

        embed = _base_embed(f"{ICO_AUTOMOD}  AutoMod Strikes  --  {member.display_name}", colour, desc)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Strike Progress", value=f"`{bar}`  `{count}/4`", inline=True)
        embed.add_field(name="Expires", value=expiry_str, inline=True)
        embed.add_field(name="Ladder", value="1 → Warn  |  2 → Timeout  |  3 → Kick  |  4 → Ban", inline=False)
        embed.set_footer(text=f"Paladin AutoMod  |  {ctx.guild.name}")
        await ctx.send(embed=embed)

    # ==========================================================================
    #  ALERTNUKE COMMANDS
    # ==========================================================================

    @commands.command(name="alertnuke", description="Toggle DM alerts for antinuke/automod events.")
    @commands.guild_only()
    @app_commands.describe(user="User to add/remove from alert list (defaults to you)")
    async def alertnuke(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        if target.id != ctx.author.id and not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can manage other users' alert subscriptions."), ephemeral=True)

        added = self._alertnuke_toggle(ctx.guild.id, target.id)
        if added:
            embed = _base_embed(f"🔔  AlertNuke -- Subscribed", COL_SUCCESS)
            embed.description = f"{ICO_SUCCESS} {target.mention} will now receive **DM alerts** whenever **Antinuke** or **AutoMod** triggers in **{ctx.guild.name}**.\n\nAlerts include: who triggered it, what action was taken, and whether it was reversed."
        else:
            embed = _base_embed(f"🔕  AlertNuke -- Unsubscribed", COL_MUTED)
            embed.description = f"{target.mention} will no longer receive DM alerts for **{ctx.guild.name}**."

        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"Managed by {ctx.author}  |  !alertnukelist to see all subscribers")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name="paladinalert", description="Toggle DM alerts for antinuke/automod events with DM confirmation.")
    @commands.guild_only()
    @app_commands.describe(user="User to add/remove from alert list (defaults to you)")
    async def paladinalert(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        if target.id != ctx.author.id and not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can manage other users' alert subscriptions."), ephemeral=True)

        added = self._alertnuke_toggle(ctx.guild.id, target.id)
        if added:
            dm_embed = _base_embed(f"🔔  Paladin Alerts Enabled  —  {ctx.guild.name}", COL_SUCCESS)
            dm_embed.description = (
                f"You are now subscribed to Paladin alerts for **{ctx.guild.name}**.\n\n"
                f"You will receive direct messages containing **context, severity, and trigger details** "
                f"whenever Antinuke or AutoMod takes action against a user.\n\n"
                f"**Bot Add events** will additionally tell you:\n"
                f"• Which bot(s) were banned\n"
                f"• Who invited them and whether their roles were stripped\n"
                f"• If the inviter was spared (and why — Owner / Whitelisted / Hardcoded Owner)"
            )
            try:
                await target.send(embed=dm_embed)
            except discord.Forbidden:
                pass

            embed = _base_embed("🔔  Alerts Subscribed", COL_SUCCESS)
            embed.description = f"{ICO_SUCCESS} {target.mention} is now subscribed to Paladin alerts for **{ctx.guild.name}**."
        else:
            embed = _base_embed("🔕  Alerts Unsubscribed", COL_MUTED)
            embed.description = f"{target.mention} will no longer receive Paladin alerts for **{ctx.guild.name}**."

        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"Managed by {ctx.author}")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name="alertnukelist", description="Show current AlertNuke subscribers for this server.")
    @commands.guild_only()
    async def alertnukelist(self, ctx: commands.Context):
        if not self._can_manage(ctx.author, ctx.guild):
            return await ctx.send(embed=_err("Only the server owner or bot owners can view AlertNuke subscribers."), ephemeral=True)

        subscribers = self._alertnuke_list(ctx.guild.id)
        embed       = _base_embed(f"🔔  AlertNuke Subscribers  —  {ctx.guild.name}", COL_BRAND)
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)

        if not subscribers:
            embed.description = "*No users are currently subscribed to alerts.*"
        else:
            lines = []
            for uid in subscribers:
                m = ctx.guild.get_member(uid)
                lines.append(f"🔔 {m.mention if m else f'<@{uid}>'} — `{uid}`")
            embed.description = "\n".join(lines)

        embed.set_footer(text=f"{len(subscribers)} subscriber(s)  |  !alertnuke @user to toggle")
        await ctx.send(embed=embed, ephemeral=True)

    # ==========================================================================
    #  BYPASS KEY SYSTEM
    # ==========================================================================

    def _find_key(self, key_str: str) -> dict | None:
        """Find a key entry by its string value."""
        for k in self._keys.get("keys", []):
            if k["key"] == key_str:
                return k
        return None

    @tasks.loop(seconds=60)
    async def _key_expiry_task(self):
        """Prune expired bypass entries from _key_bypass every 60 seconds."""
        now = time.time()
        expired = [
            k for k, v in self._key_bypass.items()
            if v["valid_until"] is not None and now >= v["valid_until"]
        ]
        for k in expired:
            del self._key_bypass[k]

    @_key_expiry_task.before_loop
    async def _key_expiry_before(self):
        await self.bot.wait_until_ready()

    @commands.command(name="generatekey", description="Generate a Paladin bypass key for this server.")
    @commands.guild_only()
    async def generatekey(self, ctx: commands.Context):
        if not self._is_protected(ctx.guild.id, ctx.author.id):
            return await ctx.send(
                embed=_err("Only bot owners, the server owner, or whitelisted users can generate bypass keys."),
                ephemeral=True,
            )

        await ctx.send(
            embed=_base_embed(f"{ICO_KEY}  Key Generation", COL_BRAND,
                              "Check your **DMs** to configure and generate a bypass key."),
            ephemeral=True,
        )

        guild_name = ctx.guild.name
        embed = _base_embed(f"{ICO_KEY}  Generate Bypass Key — {guild_name}", COL_BRAND)
        embed.description = (
            f"Configure your bypass key for **{guild_name}**.\n"
            "Select all options below, then click **Generate Key**."
        )
        embed.add_field(name="⏱️ Duration",        value="How long the key stays valid", inline=True)
        embed.add_field(name="🔢 Max Uses",        value="How many users can redeem it", inline=True)
        embed.add_field(name="💤 Idle Expiry",      value="Auto-expire when unused",      inline=True)
        embed.add_field(name=f"{ICO_SHIELD} Scope", value="Which system(s) to bypass",   inline=True)
        embed.set_footer(text="Paladin Bypass Keys  |  Key will appear after generation")

        view = _KeyGenerateView(self, ctx.guild.id, ctx.author.id)
        try:
            msg = await ctx.author.send(embed=embed, view=view)
            view._message = msg
        except discord.Forbidden:
            await ctx.send(
                embed=_err("I couldn't DM you. Please enable DMs from server members."),
                ephemeral=True,
            )

    @commands.command(name="usekey")
    @commands.guild_only()
    async def usekey(self, ctx: commands.Context, *, key: str):
        """Redeem a Paladin bypass key.  Usage: !usekey <key>"""
        # Immediately delete the command message to hide the key
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

        now      = time.time()
        key_str  = key.strip()
        key_data = self._find_key(key_str)

        if key_data is None:
            return await ctx.send(embed=_err("Invalid or expired key."), delete_after=10)

        if key_data["guild_id"] != ctx.guild.id:
            return await ctx.send(embed=_err("This key is not valid for this server."), delete_after=10)

        if key_data["expires_at"] is not None and now >= key_data["expires_at"]:
            return await ctx.send(embed=_err("This key has expired."), delete_after=10)

        if key_data["idle_expires_at"] is not None and now >= key_data["idle_expires_at"]:
            return await ctx.send(embed=_err("This key has expired due to inactivity."), delete_after=10)

        if key_data["uses_left"] is not None and key_data["uses_left"] <= 0:
            return await ctx.send(embed=_err("This key has no remaining uses."), delete_after=10)

        # ---- Redeem ----
        if key_data["uses_left"] is not None:
            key_data["uses_left"] -= 1

        if key_data["_idle_window"] is not None:
            key_data["idle_expires_at"] = now + key_data["_idle_window"]

        if ctx.author.id not in key_data["redeemers"]:
            key_data["redeemers"].append(ctx.author.id)

        _save(self._keys, KEYS_FILE, self._save_lock)

        valid_until = key_data["expires_at"]
        self._key_bypass[(ctx.guild.id, ctx.author.id)] = {
            "scope":       key_data["scope"],
            "valid_until": valid_until,
            "key":         key_data["key"],
        }

        # ---- Confirmation embed (key is NEVER shown) ----
        scope_label  = key_data["scope"].title()
        duration_str = f"<t:{int(valid_until)}:R>" if valid_until else "Until manually revoked"
        uses_str     = str(key_data["uses_left"]) if key_data["uses_left"] is not None else "Unlimited"

        embed = _base_embed(f"{ICO_KEY}  Bypass Key Activated", COL_SUCCESS)
        embed.description = (
            f"{ICO_SUCCESS} **{ctx.author.display_name}**, your bypass is now active.\n\n"
            f"> **Scope:** `{scope_label}`\n"
            f"> **Expires:** {duration_str}\n"
            f"> **Uses Remaining:** `{uses_str}`"
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text="Paladin Bypass Keys  |  Your key is not shown for security")
        await ctx.send(embed=embed, delete_after=30)

        # DM user confirmation
        dm_embed = _base_embed(f"{ICO_KEY}  Bypass Activated — {ctx.guild.name}", COL_SUCCESS)
        dm_embed.description = (
            f"Your bypass key for **{ctx.guild.name}** is now active.\n\n"
            f"> **Scope:** `{scope_label}`\n"
            f"> **Expires:** {duration_str}"
        )
        dm_embed.set_footer(text="Paladin Bypass Keys")
        try:
            await ctx.author.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        # ---- AlertNuke: notify subscribers ----
        max_display  = f"`{key_data['max_uses']}`"  if key_data["max_uses"]  is not None else "`Unlimited`"
        left_display = f"`{key_data['uses_left']}`" if key_data["uses_left"] is not None else "`Unlimited`"

        redeemers_fmt = ", ".join(f"<@{uid}>" for uid in key_data["redeemers"][:10])
        if len(key_data["redeemers"]) > 10:
            redeemers_fmt += f" ... +{len(key_data['redeemers']) - 10} more"

        created_by  = self.bot.get_user(key_data["created_by"])
        creator_str = f"{created_by.mention} (`{created_by.id}`)" if created_by else f"`{key_data['created_by']}`"

        alert_embed = _base_embed(f"{ICO_KEY}  Bypass Key Redeemed — {ctx.guild.name}", COL_WARNING)
        alert_embed.set_author(name=f"{ctx.author} ({ctx.author.id})", icon_url=ctx.author.display_avatar.url)
        alert_embed.description = f"> {ICO_KEY} A bypass key was just redeemed in **{ctx.guild.name}**."
        alert_embed.add_field(name="User",       value=f"{ctx.author.mention}\n`{ctx.author}` (`{ctx.author.id}`)", inline=True)
        alert_embed.add_field(name="Scope",      value=f"`{scope_label}`",    inline=True)
        alert_embed.add_field(name="Expires",    value=duration_str,           inline=True)
        alert_embed.add_field(name="Uses",       value=f"{left_display} / {max_display} remaining", inline=True)
        alert_embed.add_field(name="Created By", value=creator_str,            inline=True)
        alert_embed.add_field(name="Redeemers",  value=redeemers_fmt or "*None yet*", inline=False)
        alert_embed.set_footer(text="Paladin AlertNuke  |  Bypass Key System")
        await self._alertnuke_dm(ctx.guild, alert_embed)


async def setup(bot):
    await bot.add_cog(Paladin(bot))