"""
Stock Trading Cog (Production Ready)
- Asynchronous & Non-blocking architecture
- Global price caching engine to eliminate API rate limits
- Thread-safe JSON file operations
- Real-time stock data via yfinance
"""

import discord
from discord.ext import commands, tasks
import yfinance as yf
import json
import os
import asyncio
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
STARTING_BALANCE = 10_000.0            # Starting CredCoins per player
DATA_FILE = "portfolios.json"          # Persistent storage
CACHE_EXPIRY = 60                      # How long to cache stock prices (in seconds)

# ── Async Data Helpers ───────────────────────────────────────────────────────

def _sync_load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def _sync_save(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def load_data() -> dict:
    """Loads the database asynchronously without blocking the main event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_load)

async def save_data(data: dict):
    """Saves the database asynchronously using a thread executor."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_save, data)

def get_player(data: dict, user_id: str) -> dict:
    if user_id not in data:
        data[user_id] = {
            "balance": STARTING_BALANCE,
            "portfolio": {},        # {"AAPL": {"shares": 5, "avg_cost": 150.0}}
            "history": []
        }
    return data[user_id]

def fmt_cc(amount: float) -> str:
    return f"**{amount:,.2f} CC**"


class StockTrading(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_lock = asyncio.Lock()
        self.price_cache = {}  # Format: {"AAPL": {"price": 175.50, "updated_at": timestamp}}
        
        # Start the background caching engine loop
        self.update_active_tickers_cache.start()

    def cog_unload(self):
        """Clean up the background task when the cog is unloaded."""
        self.update_active_tickers_cache.cancel()

    # ── Async Price Fetcher & Cache Engine ──────────────────────────────────

    async def fetch_live_price(self, ticker: str) -> float | None:
        """Fetches stock price inside an executor thread to keep the bot completely fluid."""
        ticker = ticker.upper()
        now = datetime.utcnow().timestamp()

        # Check valid cache first
        if ticker in self.price_cache:
            if now - self.price_cache[ticker]["updated_at"] < CACHE_EXPIRY:
                return self.price_cache[ticker]["price"]

        def _fetch():
            try:
                stock = yf.Ticker(ticker)
                info = stock.fast_info
                price = info.last_price
                return round(float(price), 2) if price else None
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        price = await loop.run_in_executor(None, _fetch)

        if price is not None:
            self.price_cache[ticker] = {
                "price": price,
                "updated_at": now
            }
        return price

    @tasks.loop(seconds=60)
    async def update_active_tickers_cache(self):
        """Background task: Periodically refreshes prices for all currently owned stocks 
        to make !leaderboard and !balance commands load instantly."""
        async with self.data_lock:
            data = await load_data()
        
        active_tickers = set()
        for player in data.values():
            for ticker in player.get("portfolio", {}).keys():
                active_tickers.add(ticker.upper())
                
        if not active_tickers:
            return

        def _fetch_batch(tickers):
            cached_data = {}
            for ticker in tickers:
                try:
                    stock = yf.Ticker(ticker)
                    price = stock.fast_info.last_price
                    if price:
                        cached_data[ticker] = round(float(price), 2)
                except Exception:
                    continue
            return cached_data

        loop = asyncio.get_running_loop()
        batch_results = await loop.run_in_executor(None, _fetch_batch, active_tickers)
        
        now = datetime.utcnow().timestamp()
        for ticker, price in batch_results.items():
            self.price_cache[ticker] = {"price": price, "updated_at": now}

    @update_active_tickers_cache.before_loop
    async def before_update_cache(self):
        """Wait until the bot is ready before firing up the cache loop."""
        await self.bot.wait_until_ready()

    # ── Commands ──────────────────────────────────────────────────────────────

    @commands.command(name="stockhelp")
    async def stockhelp_cmd(self, ctx):
        """Display help documentation for trading commands."""
        embed = discord.Embed(
            title="📈 Stock Trading Bot — Commands",
            color=discord.Color.gold(),
            description="Trade real stocks with fake CredCoins (CC)!"
        )
        embed.add_field(name="!balance", value="Check your CC balance & net worth", inline=False)
        embed.add_field(name="!price <TICKER>", value="Get the live price of a stock", inline=False)
        embed.add_field(name="!buy <TICKER> <SHARES>", value="Buy shares with CC", inline=False)
        embed.add_field(name="!sell <TICKER> <SHARES>", value="Sell shares for CC", inline=False)
        embed.add_field(name="!portfolio", value="View your holdings", inline=False)
        embed.add_field(name="!leaderboard", value="Top players by net worth", inline=False)
        embed.add_field(name="!reset", value="Reset your account (fresh start)", inline=False)
        embed.set_footer(text="Prices are real-time via Yahoo Finance. Cached every 60s.")
        await ctx.send(embed=embed)

    @commands.command(name="price")
    async def price_cmd(self, ctx, ticker: str = None):
        if not ticker:
            await ctx.send("❌ Usage: `!price <TICKER>` — e.g. `!price AAPL`")
            return

        ticker = ticker.upper()
        await ctx.typing()
        price = await self.fetch_live_price(ticker)
        
        if price is None:
            await ctx.send(f"❌ Could not find stock **{ticker}**. Check the ticker symbol.")
            return

        embed = discord.Embed(
            title=f"📊 {ticker}",
            description=f"Current Price: **${price:,.2f}**",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Powered by Yahoo Finance")
        await ctx.send(embed=embed)

    @commands.command(name="balance")
    async def balance_cmd(self, ctx):
        await ctx.typing()
        async with self.data_lock:
            data = await load_data()
            player = get_player(data, str(ctx.author.id))
            await save_data(data)

        portfolio_value = 0.0
        for ticker, pos in player["portfolio"].items():
            price = await self.fetch_live_price(ticker)
            if price:
                portfolio_value += price * pos["shares"]

        net_worth = player["balance"] + portfolio_value

        embed = discord.Embed(title=f"💰 {ctx.author.display_name}'s Account", color=discord.Color.green())
        embed.add_field(name="Cash Balance", value=fmt_cc(player["balance"]), inline=True)
        embed.add_field(name="Portfolio Value", value=fmt_cc(portfolio_value), inline=True)
        embed.add_field(name="Net Worth", value=fmt_cc(net_worth), inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="buy")
    async def buy_cmd(self, ctx, ticker: str = None, shares: float = None):
        if not ticker or shares is None:
            await ctx.send("❌ Usage: `!buy <TICKER> <SHARES>` — e.g. `!buy AAPL 5`")
            return

        if shares <= 0:
            await ctx.send("❌ Shares must be a positive number.")
            return

        ticker = ticker.upper()
        await ctx.typing()
        price = await self.fetch_live_price(ticker)
        if price is None:
            await ctx.send(f"❌ Could not find stock **{ticker}**.")
            return

        cost = round(price * shares, 2)

        async with self.data_lock:
            data = await load_data()
            player = get_player(data, str(ctx.author.id))

            if player["balance"] < cost:
                await ctx.send(f"❌ Insufficient funds! You need {fmt_cc(cost)} but only have {fmt_cc(player['balance'])}.")
                return

            # Commit Transaction
            player["balance"] = round(player["balance"] - cost, 2)
            port = player["portfolio"]
            if ticker in port:
                old_shares = port[ticker]["shares"]
                old_avg = port[ticker]["avg_cost"]
                new_shares = old_shares + shares
                port[ticker]["avg_cost"] = round((old_avg * old_shares + price * shares) / new_shares, 4)
                port[ticker]["shares"] = round(new_shares, 6)
            else:
                port[ticker] = {"shares": round(shares, 6), "avg_cost": price}

            player["history"].append({
                "action": "BUY", "ticker": ticker, "shares": shares,
                "price": price, "total": cost, "time": datetime.utcnow().isoformat()
            })
            await save_data(data)

        embed = discord.Embed(title="✅ Purchase Successful", color=discord.Color.green())
        embed.add_field(name="Stock", value=ticker, inline=True)
        embed.add_field(name="Shares", value=str(shares), inline=True)
        embed.add_field(name="Price/Share", value=f"${price:,.2f}", inline=True)
        embed.add_field(name="Total Cost", value=fmt_cc(cost), inline=True)
        embed.add_field(name="Remaining Balance", value=fmt_cc(player["balance"]), inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="sell")
    async def sell_cmd(self, ctx, ticker: str = None, shares: float = None):
        if not ticker or shares is None:
            await ctx.send("❌ Usage: `!sell <TICKER> <SHARES>` — e.g. `!sell AAPL 2`")
            return

        if shares <= 0:
            await ctx.send("❌ Shares must be a positive number.")
            return

        ticker = ticker.upper()
        await ctx.typing()

        async with self.data_lock:
            data = await load_data()
            player = get_player(data, str(ctx.author.id))
            port = player["portfolio"]

            if ticker not in port or port[ticker]["shares"] < shares:
                owned = port.get(ticker, {}).get("shares", 0)
                await ctx.send(f"❌ You only own **{owned}** shares of **{ticker}**.")
                return

            price = await self.fetch_live_price(ticker)
            if price is None:
                await ctx.send(f"❌ Could not fetch price for **{ticker}**.")
                return

            proceeds = round(price * shares, 2)
            avg_cost = port[ticker]["avg_cost"]
            profit = round(proceeds - (avg_cost * shares), 2)
            profit_emoji = "📈" if profit >= 0 else "📉"

            # Commit Transaction
            port[ticker]["shares"] = round(port[ticker]["shares"] - shares, 6)
            if port[ticker]["shares"] <= 0.0001:
                del port[ticker]

            player["balance"] = round(player["balance"] + proceeds, 2)
            player["history"].append({
                "action": "SELL", "ticker": ticker, "shares": shares,
                "price": price, "total": proceeds, "profit": profit,
                "time": datetime.utcnow().isoformat()
            })
            await save_data(data)

        embed = discord.Embed(title="✅ Sale Successful", color=discord.Color.blue())
        embed.add_field(name="Stock", value=ticker, inline=True)
        embed.add_field(name="Shares Sold", value=str(shares), inline=True)
        embed.add_field(name="Price/Share", value=f"${price:,.2f}", inline=True)
        embed.add_field(name="Proceeds", value=fmt_cc(proceeds), inline=True)
        embed.add_field(name=f"{profit_emoji} P&L", value=fmt_cc(profit), inline=True)
        embed.add_field(name="New Balance", value=fmt_cc(player["balance"]), inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="portfolio")
    async def portfolio_cmd(self, ctx):
        await ctx.typing()
        async with self.data_lock:
            data = await load_data()
            player = get_player(data, str(ctx.author.id))
            await save_data(data)

        port = player["portfolio"]
        if not port:
            await ctx.send("📭 Your portfolio is empty. Use `!buy <TICKER> <SHARES>` to start trading!")
            return

        embed = discord.Embed(title=f"📁 {ctx.author.display_name}'s Portfolio", color=discord.Color.purple())
        total_value, total_invested = 0.0, 0.0

        for ticker, pos in port.items():
            price = await self.fetch_live_price(ticker)
            if price is None:
                embed.add_field(name=ticker, value="⚠️ Price unavailable", inline=False)
                continue

            value = price * pos["shares"]
            invested = pos["avg_cost"] * pos["shares"]
            pnl = value - invested
            pnl_pct = (pnl / invested * 100) if invested else 0
            arrow = "▲" if pnl >= 0 else "▼"

            total_value += value
            total_invested += invested

            embed.add_field(
                name=f"{ticker}",
                value=(
                    f"Shares: `{pos['shares']}`\n"
                    f"Avg Cost: `${pos['avg_cost']:,.2f}` | Now: `${price:,.2f}`\n"
                    f"Value: `{value:,.2f} CC` | {arrow} `{pnl:+,.2f} CC ({pnl_pct:+.1f}%)`"
                ),
                inline=False
            )

        total_pnl = total_value - total_invested
        embed.add_field(
            name="── Summary ──",
            value=f"Total Value: {fmt_cc(total_value)} | P&L: {fmt_cc(total_pnl)}",
            inline=False
        )
        await ctx.send(embed=embed)

    @commands.command(name="leaderboard")
    async def leaderboard_cmd(self, ctx):
        await ctx.typing()
        async with self.data_lock:
            data = await load_data()

        if not data:
            await ctx.send("No players yet!")
            return

        scores = []
        for uid, player in data.items():
            portfolio_value = 0.0
            for ticker, pos in player["portfolio"].items():
                price = await self.fetch_live_price(ticker)
                if price:
                    portfolio_value += price * pos["shares"]
            net_worth = player["balance"] + portfolio_value
            scores.append((uid, net_worth))

        scores.sort(key=lambda x: x[1], reverse=True)
        embed = discord.Embed(title="🏆 Leaderboard — Top Traders", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"]

        for i, (uid, worth) in enumerate(scores[:10]):
            try:
                user = await self.bot.fetch_user(int(uid))
                name = user.display_name
            except Exception:
                name = f"User {uid}"
            medal = medals[i] if i < 3 else f"#{i+1}"
            embed.add_field(name=f"{medal} {name}", value=fmt_cc(worth), inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="reset")
    async def reset_cmd(self, ctx):
        async with self.data_lock:
            data = await load_data()
            data[str(ctx.author.id)] = {
                "balance": STARTING_BALANCE,
                "portfolio": {},
                "history": []
            }
            await save_data(data)
        await ctx.send(f"🔄 **{ctx.author.display_name}** your account has been reset to {fmt_cc(STARTING_BALANCE)}. Good luck!")


async def setup(bot: commands.Bot):
    await bot.add_cog(StockTrading(bot))