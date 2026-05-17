"""
cogs/blackjack.py — Production-quality Blackjack command.

Features:
  - Full shuffled deck (6-deck shoe)
  - Ace logic (1 or 11, auto-adjusts)
  - Hit / Stand / Double Down buttons (discord.ui)
  - Dealer AI: hits on soft 17, stands on hard 17+
  - Win / Loss / Draw / Blackjack detection
  - Chip betting system (global across all servers)
  - Daily 500 chips — once per UTC day, for everyone regardless of balance
  - Global leaderboard (top 10 chip holders across all servers)
  - Timeout handling (60s inactivity ends game)
  - Clean embed UI
  - Fully async-safe
"""

import discord
import random
import json
import os
import logging

from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from utils.default import CustomContext
from utils.data import DiscordBot

log = logging.getLogger("bot.blackjack")

ACCENT   = discord.Colour.from_str("#2ECC71")
COL_WIN  = discord.Colour.from_str("#2ECC71")
COL_LOSE = discord.Colour.from_str("#E74C3C")
COL_PUSH = discord.Colour.from_str("#F39C12")
COL_BJ   = discord.Colour.from_str("#F1C40F")
COL_GOLD = discord.Colour.from_str("#FFD700")

CHIPS_FILE = "data/bj_chips.json"
DAILY_FILE = "data/bj_daily.json"   # { "user_id": "YYYY-MM-DD" }
DEFAULT_CHIPS = 1000

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

_active_games: dict = {}


_chips_cache = {}
_daily_cache = {}
_chips_dirty = False
_daily_dirty = False
_bj_lock = asyncio.Lock()

def _load_cache():
    global _chips_cache, _daily_cache
    os.makedirs("data", exist_ok=True)
    try:
        with open(CHIPS_FILE) as f:
            _chips_cache = json.load(f)
    except Exception:
        _chips_cache = {}
    try:
        with open(DAILY_FILE) as f:
            _daily_cache = json.load(f)
    except Exception:
        _daily_cache = {}

_load_cache()


async def get_chips(user_id: int) -> int:
    async with _bj_lock:
        return _chips_cache.get(str(user_id), DEFAULT_CHIPS)


async def set_chips(user_id: int, amount: int):
    global _chips_dirty
    async with _bj_lock:
        _chips_cache[str(user_id)] = max(0, amount)
        _chips_dirty = True


async def adjust_chips(user_id: int, delta: int) -> int:
    global _chips_dirty
    async with _bj_lock:
        uid = str(user_id)
        new = max(0, _chips_cache.get(uid, DEFAULT_CHIPS) + delta)
        _chips_cache[uid] = new
        _chips_dirty = True
        return new


# ── Daily cooldown ─────────────────────────────────────────────────────────────

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def can_claim_daily(user_id: int) -> bool:
    """True if the user has not yet claimed today (UTC calendar day)."""
    async with _bj_lock:
        return _daily_cache.get(str(user_id)) != _today_utc()


async def mark_daily_claimed(user_id: int):
    global _daily_dirty
    async with _bj_lock:
        _daily_cache[str(user_id)] = _today_utc()
        _daily_dirty = True


def next_reset_timestamp() -> int:
    """Unix timestamp of next UTC midnight."""
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


# ── Global leaderboard ─────────────────────────────────────────────────────────

def get_leaderboard(top: int = 10) -> list[tuple[str, int]]:
    data = dict(_chips_cache)
    return sorted(data.items(), key=lambda x: x[1], reverse=True)[:top]


# ── Deck & hand logic ──────────────────────────────────────────────────────────

def _build_shoe(decks: int = 6) -> list:
    deck = [f"{rank}{suit}" for suit in SUITS for rank in RANKS]
    shoe = deck * decks
    random.shuffle(shoe)
    return shoe


def _card_value(card: str) -> int:
    rank = card[:-1]
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def _hand_value(hand: list) -> int:
    total, aces = 0, 0
    for card in hand:
        total += _card_value(card)
        if card[:-1] == "A":
            aces += 1
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _is_soft(hand: list) -> bool:
    hard = sum(10 if c[:-1] in ("J","Q","K") else (1 if c[:-1]=="A" else int(c[:-1])) for c in hand)
    return _hand_value(hand) != hard


def _hand_str(hand: list, hide_second: bool = False) -> str:
    if hide_second and len(hand) >= 2:
        return f"` {hand[0]} `  ` 🂠 `"
    return "  ".join(f"` {c} `" for c in hand)


def _is_blackjack(hand: list) -> bool:
    return len(hand) == 2 and _hand_value(hand) == 21


# ── Game state ─────────────────────────────────────────────────────────────────

class BlackjackGame:
    def __init__(self, user_id: int, bet: int):
        self.user_id = user_id
        self.bet = bet
        self.shoe = _build_shoe()
        self.player_hand: list = []
        self.dealer_hand: list = []
        self.done = False
        self.result: str | None = None
        self.chips_delta = 0

        self.player_hand = [self._draw(), self._draw()]
        self.dealer_hand = [self._draw(), self._draw()]

    def _draw(self) -> str:
        if not self.shoe:
            self.shoe = _build_shoe()
        return self.shoe.pop()

    def player_value(self) -> int:
        return _hand_value(self.player_hand)

    def dealer_value(self) -> int:
        return _hand_value(self.dealer_hand)

    def hit(self):
        self.player_hand.append(self._draw())

    def double_down(self):
        self.player_hand.append(self._draw())

    def dealer_play(self):
        while True:
            val = self.dealer_value()
            if val < 17 or (val == 17 and _is_soft(self.dealer_hand)):
                self.dealer_hand.append(self._draw())
            else:
                break

    def resolve(self, doubled: bool = False):
        pv, dv = self.player_value(), self.dealer_value()
        bet = self.bet * 2 if doubled else self.bet

        if pv > 21:
            self.result, self.chips_delta = "bust", -bet
        elif _is_blackjack(self.player_hand) and not _is_blackjack(self.dealer_hand):
            self.result, self.chips_delta = "blackjack", int(bet * 1.5)
        elif _is_blackjack(self.dealer_hand) and not _is_blackjack(self.player_hand):
            self.result, self.chips_delta = "lose", -bet
        elif dv > 21 or pv > dv:
            self.result, self.chips_delta = "win", bet
        elif pv < dv:
            self.result, self.chips_delta = "lose", -bet
        else:
            self.result, self.chips_delta = "push", 0

        self.done = True


# ── UI View ────────────────────────────────────────────────────────────────────

class BlackjackView(discord.ui.View):
    def __init__(self, game: BlackjackGame, user: discord.Member | discord.User):
        super().__init__(timeout=60)
        self.game = game
        self.user = user
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your game!", ephemeral=True)
            return False
        return True

    async def _make_embed(self, final: bool = False) -> discord.Embed:
        game = self.game
        pv, dv = game.player_value(), game.dealer_value()

        if not final:
            colour, title = ACCENT, "🃏  Blackjack"
            dealer_str = _hand_str(game.dealer_hand, hide_second=True)
            dealer_label = f"Dealer's Hand ({_card_value(game.dealer_hand[0])}+?)"
        else:
            result = game.result
            colour = {"win": COL_WIN, "blackjack": COL_BJ, "push": COL_PUSH}.get(result, COL_LOSE)
            title = {
                "win": "🎉  You Win!",
                "blackjack": "🃏  Blackjack! Natural 21!",
                "push": "🤝  Push — It's a Tie!",
                "lose": "💀  You Lose",
                "bust": "💥  Bust! Over 21!",
            }.get(result, "🃏  Game Over")
            dealer_str = _hand_str(game.dealer_hand)
            dealer_label = f"Dealer's Hand ({dv})"

        embed = discord.Embed(title=title, colour=colour)
        embed.add_field(name=f"Your Hand ({pv})", value=_hand_str(game.player_hand), inline=False)
        embed.add_field(name=dealer_label, value=dealer_str, inline=False)

        if game.bet > 0:
            chips = await get_chips(game.user_id)
            footer = f"Bet: {game.bet:,} chips"
            if final and game.chips_delta != 0:
                sign = "+" if game.chips_delta > 0 else ""
                footer += f"  |  {sign}{game.chips_delta:,}  →  {chips:,} chips"
            embed.set_footer(text=footer)

        return embed

    async def _end_game(self, interaction: discord.Interaction, doubled: bool = False):
        self.game.dealer_play()
        self.game.resolve(doubled=doubled)
        if self.game.bet > 0:
            await adjust_chips(self.game.user_id, self.game.chips_delta)
        _active_games.pop(self.game.user_id, None)
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(embed=await self._make_embed(final=True), view=self)

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def on_timeout(self):
        if self.game.bet > 0:
            await adjust_chips(self.game.user_id, self.game.bet)
        _active_games.pop(self.game.user_id, None)
        self._disable_all()
        if self.message:
            try:
                await self.message.edit(
                    embed=discord.Embed(description="⏱️ Game timed out.", colour=discord.Colour.orange()),
                    view=self,
                )
            except discord.NotFound:
                pass

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="👊")
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.game.hit()
        if self.game.player_value() >= 21:
            await self._end_game(interaction)
        else:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.label == "Double":
                    item.disabled = True
            await interaction.response.edit_message(embed=await self._make_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._end_game(interaction)

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success, emoji="💰")
    async def double_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.bet > 0 and await get_chips(self.game.user_id) < self.game.bet:
            return await interaction.response.send_message(
                f"❌ Not enough chips to double! You need **{self.game.bet:,}** more chips.",
                ephemeral=True,
            )
        if self.game.bet > 0:
            await adjust_chips(self.game.user_id, -self.game.bet)
        self.game.double_down()
        await self._end_game(interaction, doubled=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class Blackjack(commands.Cog, name="Blackjack"):
    """Play Blackjack with chip betting, a daily reward, and a global leaderboard."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self.save_task.start()

    def cog_unload(self):
        self.save_task.cancel()
        # Flush any dirty data synchronously on shutdown
        if _chips_dirty:
            try:
                with open(CHIPS_FILE, "w") as f:
                    json.dump(_chips_cache, f, indent=2)
            except Exception:
                pass
        if _daily_dirty:
            try:
                with open(DAILY_FILE, "w") as f:
                    json.dump(_daily_cache, f, indent=2)
            except Exception:
                pass

    @tasks.loop(seconds=60)
    async def save_task(self):
        global _chips_dirty, _daily_dirty
        dirty_chips = False
        dirty_daily = False
        async with _bj_lock:
            if _chips_dirty:
                dirty_chips = True
                _chips_dirty = False
            if _daily_dirty:
                dirty_daily = True
                _daily_dirty = False
        
        if dirty_chips:
            def sync_save_chips():
                tmp = CHIPS_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(_chips_cache, f, indent=2)
                os.replace(tmp, CHIPS_FILE)
            await asyncio.to_thread(sync_save_chips)
                
        if dirty_daily:
            def sync_save_daily():
                tmp = DAILY_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(_daily_cache, f, indent=2)
                os.replace(tmp, DAILY_FILE)
            await asyncio.to_thread(sync_save_daily)

    def _start_embed(self, game: BlackjackGame, user, chips: int) -> discord.Embed:
        embed = discord.Embed(title="🃏  Blackjack", colour=ACCENT)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name=f"Your Hand ({game.player_value()})", value=_hand_str(game.player_hand), inline=False)
        embed.add_field(
            name=f"Dealer's Hand ({_card_value(game.dealer_hand[0])}+?)",
            value=_hand_str(game.dealer_hand, hide_second=True),
            inline=False,
        )
        if game.bet > 0:
            embed.set_footer(text=f"Bet: {game.bet:,}  |  Balance: {chips:,} chips")
        embed.description = "Hit, Stand, or Double Down!"
        return embed

    async def _start_game(self, user, bet: int, send_fn, ephemeral_fn=None):
        uid = user.id
        err = lambda m: ephemeral_fn(m) if ephemeral_fn else send_fn(m)

        if uid in _active_games:
            return await err("❌ You already have an active Blackjack game! Finish it first.")
        if bet < 0:
            return await err("❌ Bet must be 0 or more chips.")
        
        chips = await get_chips(uid)
        if bet > 0:
            if chips < bet:
                return await err(f"❌ Not enough chips! You have **{chips:,}** but tried to bet **{bet:,}**.")
            await adjust_chips(uid, -bet)

        game = BlackjackGame(user_id=uid, bet=bet)
        _active_games[uid] = game

        if _is_blackjack(game.player_hand):
            game.dealer_play()
            game.resolve()
            if bet > 0:
                await adjust_chips(uid, game.chips_delta + bet)
            _active_games.pop(uid, None)
            view = BlackjackView(game, user)
            view._disable_all()
            return await send_fn(embed=await view._make_embed(final=True))

        view = BlackjackView(game, user)
        msg = await send_fn(embed=self._start_embed(game, user, chips - bet if bet > 0 else chips), view=view)
        view.message = msg

    # ── !blackjack / /blackjack ───────────────────────────────────────────────

    @commands.command(aliases=["bj"])
    @commands.guild_only()
    @commands.max_concurrency(1, per=commands.BucketType.user)
    async def blackjack(self, ctx: CustomContext, bet: int = 0):
        """Play Blackjack! Optionally bet chips. Usage: !blackjack [bet]"""
        await self._start_game(ctx.author, bet, ctx.send)

    @app_commands.command(name="blackjack", description="Play a game of Blackjack!")
    @app_commands.describe(bet="Chips to bet (default 0)")
    @app_commands.guild_only()
    async def slash_blackjack(self, interaction: discord.Interaction, bet: int = 0):
        await interaction.response.defer()
        await self._start_game(
            interaction.user, bet,
            interaction.followup.send,
            ephemeral_fn=lambda m: interaction.followup.send(m, ephemeral=True),
        )

    # ── !bjbalance / /bjbalance ───────────────────────────────────────────────

    @commands.command(aliases=["chips", "balance", "bjbal"])
    async def bjbalance(self, ctx: CustomContext, user: discord.Member = None):
        """Check your (or someone else's) Blackjack chip balance."""
        target = user or ctx.author
        embed = discord.Embed(
            title="🃏 Blackjack Balance",
            description=f"**{target.display_name}** has **{await get_chips(target.id):,}** chips.",
            colour=ACCENT,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @app_commands.command(name="bjbalance", description="Check Blackjack chip balance.")
    @app_commands.describe(user="Whose balance to check (default: yours)")
    async def slash_bjbalance(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        embed = discord.Embed(
            title="🃏 Blackjack Balance",
            description=f"**{target.display_name}** has **{await get_chips(target.id):,}** chips.",
            colour=ACCENT,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── !bjdaily / /bjdaily ───────────────────────────────────────────────────

    @commands.command(aliases=["bjdaily", "blackjackdaily"])
    async def daily(self, ctx: CustomContext):
        """Claim 500 free chips once per day (UTC midnight reset). Everyone qualifies."""
        uid = ctx.author.id
        if not await can_claim_daily(uid):
            ts = next_reset_timestamp()
            return await ctx.send(embed=discord.Embed(
                title="⏳ Already Claimed",
                description=f"You already claimed today!\nResets <t:{ts}:R>",
                colour=COL_LOSE,
            ))
        await mark_daily_claimed(uid)
        new_bal = await adjust_chips(uid, 500)
        embed = discord.Embed(
            title="🎁 Daily Chips Claimed!",
            description=f"**+500 chips!**\nNew balance: **{new_bal:,}** chips",
            colour=COL_WIN,
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Resets at UTC midnight  •  Come back tomorrow!")
        await ctx.send(embed=embed)

    @app_commands.command(name="bjdaily", description="Claim 500 free chips once per day.")
    async def slash_daily(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if not await can_claim_daily(uid):
            ts = next_reset_timestamp()
            return await interaction.response.send_message(embed=discord.Embed(
                title="⏳ Already Claimed",
                description=f"You already claimed today!\nResets <t:{ts}:R>",
                colour=COL_LOSE,
            ), ephemeral=True)
        await mark_daily_claimed(uid)
        new_bal = await adjust_chips(uid, 500)
        embed = discord.Embed(
            title="🎁 Daily Chips Claimed!",
            description=f"**+500 chips!**\nNew balance: **{new_bal:,}** chips",
            colour=COL_WIN,
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="Resets at UTC midnight  •  Come back tomorrow!")
        await interaction.response.send_message(embed=embed)

    # ── !bjleaderboard / /bjleaderboard ──────────────────────────────────────

    @commands.command(aliases=["bjlb", "bjtop", "blackjacklb"])
    async def bjleaderboard(self, ctx: CustomContext):
        """Global Blackjack chip leaderboard — top 10 across all servers."""
        await ctx.send(embed=await self._lb_embed(ctx.bot))

    @app_commands.command(name="bjleaderboard", description="Global Blackjack chip leaderboard — top 10 players.")
    async def slash_bjleaderboard(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=await self._lb_embed(interaction.client))

    async def _lb_embed(self, bot) -> discord.Embed:
        entries = get_leaderboard(top=10)
        embed = discord.Embed(
            title="🏆  Blackjack — Global Leaderboard",
            colour=COL_GOLD,
        )
        if not entries:
            embed.description = "No chip data yet. Play some Blackjack!"
            return embed

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, (uid, chips) in enumerate(entries, 1):
            user = bot.get_user(int(uid))
            name = user.display_name if user else f"Unknown ({uid})"
            rank = medals.get(i, f"`#{i}`")
            lines.append(f"{rank} **{name}** — {chips:,} chips")

        embed.description = "\n".join(lines)
        total = len(_chips_cache)
        embed.set_footer(text=f"Global  •  {total} players tracked")
        return embed


async def setup(bot):
    await bot.add_cog(Blackjack(bot))
