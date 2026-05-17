"""
cogs/giveaway.py — Advanced giveaway system with button entry,
multi-winner, role requirements, reroll, and persistent storage.
"""

import discord
import asyncio
import json
import os
import random
import uuid

from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks
from discord import app_commands
from utils.default import CustomContext
from utils.data import DiscordBot

ACCENT = discord.Colour.from_str("#9B59B6")
GIVEAWAYS_FILE = "data/giveaways_v2.json"

_ga_cache: dict[str, list] = {}   # guild_id -> [giveaway_dicts]
_ga_dirty = False
_ga_lock = asyncio.Lock()


def _ensure_data():
    os.makedirs("data", exist_ok=True)


def _load():
    global _ga_cache
    _ensure_data()
    try:
        with open(GIVEAWAYS_FILE) as f:
            _ga_cache = json.load(f)
    except Exception:
        _ga_cache = {}


_load()


# ── Time parsing ──────────────────────────────────────────────────────────────

def _parse_duration(raw: str) -> tuple[int | None, str | None]:
    """Parse durations like 10s, 5m, 2h, 1d, 1w or combos like 1d12h."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    raw = raw.lower().strip()
    total = 0
    buf = ""
    for ch in raw:
        if ch.isdigit():
            buf += ch
        elif ch in units:
            if not buf:
                return None, "❌ Invalid duration format. Use `10s`, `5m`, `2h`, `1d`, `1w`."
            total += int(buf) * units[ch]
            buf = ""
        else:
            return None, f"❌ Unknown unit `{ch}`. Valid: `s`, `m`, `h`, `d`, `w`."
    if buf:
        return None, "❌ Duration must end with a unit (s/m/h/d/w). Example: `1h30m`."
    if total <= 0:
        return None, "❌ Duration must be greater than 0."
    if total > 30 * 86400:
        return None, "❌ Maximum giveaway duration is **30 days**."
    return total, None


def _fmt_duration(seconds: int) -> str:
    parts = []
    for label, div in [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]:
        if seconds >= div:
            val, seconds = divmod(seconds, div)
            parts.append(f"{val}{label}")
    return " ".join(parts) or "0s"


# ── Giveaway Embed Builder ────────────────────────────────────────────────────

def _build_giveaway_embed(g: dict, ended: bool = False, winners: list[discord.Member] = None) -> discord.Embed:
    if ended:
        if winners:
            winner_text = ", ".join(w.mention for w in winners)
            embed = discord.Embed(
                title="🎉  Giveaway Ended!",
                description=f"**Prize:** {g['prize']}\n\n🏆 **Winner{'s' if len(winners) > 1 else ''}:** {winner_text}",
                colour=discord.Colour.dark_grey(),
            )
        else:
            embed = discord.Embed(
                title="🎉  Giveaway Ended!",
                description=f"**Prize:** {g['prize']}\n\n😢 No valid entries.",
                colour=discord.Colour.dark_grey(),
            )
        embed.set_footer(text=f"Giveaway ID: {g['id'][:8]} • Ended")
        return embed

    entry_count = len(g.get("entries", []))
    lines = [
        f"**Prize:** {g['prize']}",
        f"**Winners:** {g['winner_count']}",
        f"**Entries:** {entry_count}",
        f"**Ends:** <t:{int(g['fire_at'])}:R> (<t:{int(g['fire_at'])}:f>)",
    ]
    if g.get("required_role_id"):
        lines.append(f"**Required Role:** <@&{g['required_role_id']}>")
    lines.append("\nClick the 🎉 button below to enter!")

    embed = discord.Embed(
        title=f"🎉  GIVEAWAY",
        description="\n".join(lines),
        colour=ACCENT,
    )
    embed.set_footer(text=f"Hosted by {g['host_name']} • ID: {g['id'][:8]}")
    embed.timestamp = datetime.fromtimestamp(g["fire_at"], tz=timezone.utc)
    return embed


# ── Persistent Button View ───────────────────────────────────────────────────

class GiveawayButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Enter Giveaway",
            emoji="🎉",
            custom_id="giveaway:enter",
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        msg_id = interaction.message.id

        # Find the giveaway
        g = None
        async with _ga_lock:
            for ga in _ga_cache.get(guild_id, []):
                if ga.get("message_id") == msg_id:
                    g = ga
                    break

        if not g:
            return await interaction.response.send_message(
                "❌ This giveaway has already ended or doesn't exist.", ephemeral=True
            )

        user_id = interaction.user.id

        # Check role requirement
        if g.get("required_role_id"):
            role = interaction.guild.get_role(g["required_role_id"])
            if role and role not in interaction.user.roles:
                return await interaction.response.send_message(
                    f"❌ You need the **{role.name}** role to enter this giveaway!", ephemeral=True
                )

        # Toggle entry
        async with _ga_lock:
            global _ga_dirty
            entries = g.setdefault("entries", [])
            if user_id in entries:
                entries.remove(user_id)
                _ga_dirty = True
                await interaction.response.send_message(
                    "🚪 You have **left** the giveaway.", ephemeral=True
                )
            else:
                entries.append(user_id)
                _ga_dirty = True
                await interaction.response.send_message(
                    "✅ You have **entered** the giveaway! Good luck! 🍀", ephemeral=True
                )

        # Update embed with new entry count
        embed = _build_giveaway_embed(g)
        try:
            await interaction.message.edit(embed=embed)
        except discord.HTTPException:
            pass


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GiveawayButton())


# ── Cog ───────────────────────────────────────────────────────────────────────

class Giveaway(commands.Cog):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self._tasks: dict[str, asyncio.Task] = {}
        self.bot.add_view(GiveawayView())  # Persistent view
        self.restore_giveaways.start()
        self.save_loop.start()

    def cog_unload(self):
        self.save_loop.cancel()
        self.restore_giveaways.cancel()
        for t in self._tasks.values():
            t.cancel()
        if _ga_dirty:
            _sync_save()

    # ── Persistence ───────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def save_loop(self):
        global _ga_dirty
        async with _ga_lock:
            if _ga_dirty:
                _ga_dirty = False
                await asyncio.to_thread(_sync_save)

    @tasks.loop(count=1)
    async def restore_giveaways(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc).timestamp()
        async with _ga_lock:
            all_gas = []
            for guild_id, gas in _ga_cache.items():
                for g in gas:
                    all_gas.append((guild_id, g))

        for guild_id, g in all_gas:
            delay = g["fire_at"] - now
            if delay <= 0:
                self.bot.loop.create_task(self._end_giveaway(guild_id, g))
            else:
                self._tasks[g["id"]] = self.bot.loop.create_task(
                    self._schedule(guild_id, g, delay)
                )

    # ── Internal ──────────────────────────────────────────────────

    async def _schedule(self, guild_id: str, g: dict, delay: float):
        await asyncio.sleep(delay)
        await self._end_giveaway(guild_id, g)

    async def _end_giveaway(self, guild_id: str, g: dict):
        global _ga_dirty
        self._tasks.pop(g["id"], None)

        channel = self.bot.get_channel(g["channel_id"])
        if not channel:
            async with _ga_lock:
                _ga_cache.get(guild_id, [])[:] = [
                    x for x in _ga_cache.get(guild_id, []) if x["id"] != g["id"]
                ]
                _ga_dirty = True
            return

        # Pick winners from entries
        entries = g.get("entries", [])
        guild = channel.guild
        valid_members = []
        for uid in entries:
            m = guild.get_member(uid)
            if m and not m.bot:
                if g.get("required_role_id"):
                    role = guild.get_role(g["required_role_id"])
                    if role and role not in m.roles:
                        continue
                valid_members.append(m)

        winner_count = min(g.get("winner_count", 1), len(valid_members))
        winners = random.sample(valid_members, winner_count) if valid_members else []

        # Update the original message
        try:
            msg = await channel.fetch_message(g["message_id"])
            embed = _build_giveaway_embed(g, ended=True, winners=winners)
            # Remove the button
            await msg.edit(embed=embed, view=None)
        except discord.NotFound:
            pass

        # Announce winners
        if winners:
            winner_mentions = ", ".join(w.mention for w in winners)
            announce_embed = discord.Embed(
                title="🎊  Congratulations!",
                description=f"{winner_mentions} won **{g['prize']}**!",
                colour=discord.Colour.gold(),
            )
            announce_embed.set_footer(text=f"Giveaway ID: {g['id'][:8]}")
            await channel.send(
                content=f"🎉 {winner_mentions}",
                embed=announce_embed,
            )
        else:
            await channel.send(
                embed=discord.Embed(
                    description=f"😢 Nobody entered the giveaway for **{g['prize']}**.",
                    colour=discord.Colour.dark_grey(),
                )
            )

        # Remove from cache
        async with _ga_lock:
            if guild_id in _ga_cache:
                _ga_cache[guild_id] = [x for x in _ga_cache[guild_id] if x["id"] != g["id"]]
            _ga_dirty = True

    # ── Help Embed ────────────────────────────────────────────────

    def help_embed(self, prefix: str = "!", guild=None) -> discord.Embed:
        embed = discord.Embed(
            title="🎉  Giveaways",
            description=(
                "Create and manage advanced giveaways with button-based entry, "
                "multi-winner support, role requirements, and reroll."
            ),
            colour=ACCENT,
        )
        embed.add_field(
            name="📌 Start a Giveaway",
            value=(
                f"`{prefix}giveaway <duration> <prize> [winners] [required_role]`\n"
                f"`/giveaway start`\n\n"
                f"**Examples:**\n"
                f"`{prefix}giveaway 1h Nitro` — 1 winner, no role required\n"
                f"`{prefix}giveaway 2d Discord Nitro 3` — 3 winners\n"
                f"`{prefix}giveaway 1d12h VIP Role 1 @Members` — requires Members role"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔧 Manage",
            value=(
                f"`{prefix}giveaway end <id>` — Force-end a running giveaway\n"
                f"`{prefix}giveaway reroll <id>` — Re-roll a new winner\n"
                f"`{prefix}giveaway list` — Show all active giveaways"
            ),
            inline=False,
        )
        embed.add_field(
            name="⏱️ Duration Formats",
            value="`10s` `5m` `2h` `1d` `1w` `1d12h` (max 30 days)",
            inline=False,
        )

        if guild:
            guild_id = str(guild.id)
            active = _ga_cache.get(guild_id, [])
            if active:
                lines = []
                for g in active[:5]:
                    lines.append(f"`{g['id'][:8]}` — **{g['prize']}** (ends <t:{int(g['fire_at'])}:R>)")
                embed.add_field(
                    name=f"📋 Active Giveaways ({len(active)})",
                    value="\n".join(lines),
                    inline=False,
                )

        embed.set_footer(text="Requires: manage_messages permission to create giveaways")
        return embed

    # ── Commands ──────────────────────────────────────────────────

    @commands.hybrid_group(name="giveaway", description="Giveaway commands.", fallback="start")
    @app_commands.describe(
        duration="Duration (e.g. 10m, 1h, 2d, 1w, 1d12h)",
        prize="The prize to give away",
        winners="Number of winners (default: 1)",
        required_role="Role required to enter (optional)",
    )
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def giveaway_cmd(
        self, ctx: CustomContext,
        duration: str,
        prize: str,
        winners: int = 1,
        required_role: discord.Role = None,
    ):
        """Start a giveaway with optional multi-winners and role requirements."""
        seconds, error = _parse_duration(duration)
        if error:
            return await ctx.send(embed=discord.Embed(description=error, colour=discord.Colour.red()))

        if winners < 1 or winners > 20:
            return await ctx.send(embed=discord.Embed(
                description="❌ Winner count must be between **1** and **20**.",
                colour=discord.Colour.red(),
            ))

        gid = str(uuid.uuid4())
        fire_at = datetime.now(timezone.utc).timestamp() + seconds

        g = {
            "id": gid,
            "channel_id": ctx.channel.id,
            "message_id": None,  # filled after send
            "guild_id": ctx.guild.id,
            "prize": prize,
            "winner_count": winners,
            "host_id": ctx.author.id,
            "host_name": str(ctx.author),
            "required_role_id": required_role.id if required_role else None,
            "fire_at": fire_at,
            "entries": [],
        }

        embed = _build_giveaway_embed(g)
        view = GiveawayView()

        # If prefix command, try to delete the invocation
        if not ctx.interaction:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass

        msg = await ctx.send(embed=embed, view=view)
        g["message_id"] = msg.id

        guild_id = str(ctx.guild.id)
        global _ga_dirty
        async with _ga_lock:
            _ga_cache.setdefault(guild_id, []).append(g)
            _ga_dirty = True

        self._tasks[gid] = self.bot.loop.create_task(
            self._schedule(guild_id, g, seconds)
        )

        if ctx.interaction:
            await ctx.interaction.followup.send(
                embed=discord.Embed(
                    description=f"✅ Giveaway for **{prize}** started! Ends <t:{int(fire_at)}:R>.",
                    colour=discord.Colour.green(),
                ),
                ephemeral=True,
            )

    @giveaway_cmd.command(name="reroll", description="Re-roll a giveaway winner.")
    @app_commands.describe(giveaway_id="The 8-character giveaway ID (shown in footer)")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def reroll(self, ctx: CustomContext, giveaway_id: str):
        """Re-roll a winner for a recently ended giveaway by its ID."""
        # We need to find the original message — search recent messages
        found = False
        async for msg in ctx.channel.history(limit=100):
            if msg.author.id != self.bot.user.id or not msg.embeds:
                continue
            embed = msg.embeds[0]
            if embed.footer and embed.footer.text and giveaway_id.lower() in embed.footer.text.lower():
                # Found the giveaway message — get reactions or stored entries
                # Try to find entries from the embed description or reaction
                users = []
                for reaction in msg.reactions:
                    if str(reaction.emoji) == "🎉":
                        users = [u async for u in reaction.users() if not u.bot and u.id != self.bot.user.id]
                        break

                if not users:
                    return await ctx.send(embed=discord.Embed(
                        description="❌ No valid entries found for this giveaway.",
                        colour=discord.Colour.red(),
                    ))

                winner = random.choice(users)
                await ctx.send(embed=discord.Embed(
                    title="🔄  Giveaway Re-rolled!",
                    description=f"🎊 New winner: {winner.mention}!",
                    colour=discord.Colour.gold(),
                ))
                found = True
                break

        if not found:
            await ctx.send(embed=discord.Embed(
                description=f"❌ Could not find a giveaway with ID `{giveaway_id}` in the last 100 messages.",
                colour=discord.Colour.red(),
            ))

    @giveaway_cmd.command(name="end", description="Force-end a running giveaway.")
    @app_commands.describe(giveaway_id="The 8-character giveaway ID (shown in footer)")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def end_giveaway(self, ctx: CustomContext, giveaway_id: str):
        """Immediately end a running giveaway and pick winners."""
        guild_id = str(ctx.guild.id)
        target = None
        async with _ga_lock:
            for g in _ga_cache.get(guild_id, []):
                if g["id"].startswith(giveaway_id.lower()):
                    target = g
                    break

        if not target:
            return await ctx.send(embed=discord.Embed(
                description=f"❌ No active giveaway found with ID `{giveaway_id}`.",
                colour=discord.Colour.red(),
            ))

        # Cancel the scheduled task
        task = self._tasks.pop(target["id"], None)
        if task:
            task.cancel()

        await self._end_giveaway(guild_id, target)
        await ctx.send(embed=discord.Embed(
            description=f"✅ Giveaway `{giveaway_id}` has been ended.",
            colour=discord.Colour.green(),
        ), delete_after=10)

    @giveaway_cmd.command(name="list", description="Show all active giveaways in this server.")
    @commands.guild_only()
    async def list_giveaways(self, ctx: CustomContext):
        """List all running giveaways in this server."""
        guild_id = str(ctx.guild.id)
        gas = _ga_cache.get(guild_id, [])

        if not gas:
            return await ctx.send(embed=discord.Embed(
                description="📋 There are no active giveaways in this server.",
                colour=discord.Colour.greyple(),
            ))

        embed = discord.Embed(
            title="🎉  Active Giveaways",
            colour=ACCENT,
        )
        for g in gas:
            entries = len(g.get("entries", []))
            role_text = f" • Role: <@&{g['required_role_id']}>" if g.get("required_role_id") else ""
            embed.add_field(
                name=f"`{g['id'][:8]}` — {g['prize']}",
                value=(
                    f"**Ends:** <t:{int(g['fire_at'])}:R>\n"
                    f"**Winners:** {g['winner_count']} • **Entries:** {entries}{role_text}\n"
                    f"**Channel:** <#{g['channel_id']}>"
                ),
                inline=False,
            )
        embed.set_footer(text=f"{len(gas)} active giveaway{'s' if len(gas) != 1 else ''}")
        await ctx.send(embed=embed)


def _sync_save():
    _ensure_data()
    tmp = GIVEAWAYS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_ga_cache, f, indent=2)
    os.replace(tmp, GIVEAWAYS_FILE)


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
