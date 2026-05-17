import discord
import asyncio
import random
import aiohttp
import html
import json
import os
import uuid

from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks
from discord import app_commands
from utils.default import CustomContext
from utils.data import DiscordBot

ACCENT = discord.Colour.from_str("#5865F2")
snipe_cache: dict = {}

REMINDERS_FILE = "data/reminders.json"

_reminders_cache = []
_extras_dirty_r = False
_extras_lock = asyncio.Lock()

def _load_extras():
    global _reminders_cache
    os.makedirs("data", exist_ok=True)
    try:
        with open(REMINDERS_FILE) as f:
            _reminders_cache = json.load(f)
    except Exception:
        pass

_load_extras()

async def _add_reminder(rid: str, user_id: int, channel_id: int, guild_id: int, text: str, fire_at: float):
    global _extras_dirty_r
    async with _extras_lock:
        _reminders_cache.append({
            "id":         rid,
            "user_id":    user_id,
            "channel_id": channel_id,
            "guild_id":   guild_id,
            "text":       text,
            "fire_at":    fire_at,
            "set_at":     datetime.now(timezone.utc).timestamp(),
        })
        _extras_dirty_r = True

async def _remove_reminder(rid: str):
    global _reminders_cache, _extras_dirty_r
    async with _extras_lock:
        _reminders_cache = [r for r in _reminders_cache if r["id"] != rid]
        _extras_dirty_r = True


# ─── Tic Tac Toe ────────────────────────────────────────────────
class TTTButton(discord.ui.Button):
    def __init__(self, row: int, col: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=row)
        self.row_pos = row
        self.col_pos = col

    async def callback(self, interaction: discord.Interaction):
        view: TTTView = self.view
        if interaction.user.id != view.current_player.id:
            return await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
        self.label = view.current_symbol
        self.style = discord.ButtonStyle.danger if view.current_symbol == "❌" else discord.ButtonStyle.primary
        self.disabled = True
        view.board[self.row_pos][self.col_pos] = view.current_symbol
        if view.check_winner():
            view.stop()
            for item in view.children: item.disabled = True
            return await interaction.response.edit_message(
                embed=discord.Embed(title="🎮  Tic Tac Toe", description=f"🎉 **{interaction.user.display_name}** wins!", colour=discord.Colour.green()), view=view)
        if view.is_full():
            view.stop()
            for item in view.children: item.disabled = True
            return await interaction.response.edit_message(
                embed=discord.Embed(title="🎮  Tic Tac Toe", description="🤝 It's a **tie**!", colour=discord.Colour.gold()), view=view)
        view.switch_turn()
        await interaction.response.edit_message(
            embed=discord.Embed(title="🎮  Tic Tac Toe",
                description=f"It's **{view.current_player.display_name}**'s turn ({view.current_symbol})", colour=ACCENT), view=view)


class TTTView(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=120)
        self.player1 = p1
        self.player2 = p2
        self.current_player = p1
        self.current_symbol = "❌"
        self.board = [["" for _ in range(3)] for _ in range(3)]
        self.message = None
        for r in range(3):
            for c in range(3):
                self.add_item(TTTButton(r, c))

    def switch_turn(self):
        self.current_player = self.player2 if self.current_player == self.player1 else self.player1
        self.current_symbol = "⭕" if self.current_symbol == "❌" else "❌"

    def check_winner(self):
        b = self.board
        for line in ([b[r] for r in range(3)] + [[b[r][c] for r in range(3)] for c in range(3)] +
                     [[b[0][0], b[1][1], b[2][2]], [b[0][2], b[1][1], b[2][0]]]):
            if line[0] and line[0] == line[1] == line[2]:
                return line[0]
        return None

    def is_full(self):
        return all(self.board[r][c] for r in range(3) for c in range(3))

    async def on_timeout(self):
        for item in self.children: item.disabled = True
        if self.message:
            try: await self.message.edit(view=self)
            except discord.NotFound: pass


# ─── Trivia ─────────────────────────────────────────────────────
class TriviaView(discord.ui.View):
    def __init__(self, correct, all_answers, invoker_id):
        super().__init__(timeout=20)
        self.correct = correct
        self.invoker_id = invoker_id
        self.answered = False
        self.message = None
        random.shuffle(all_answers)
        for answer in all_answers:
            btn = discord.ui.Button(label=answer, style=discord.ButtonStyle.secondary)
            btn.callback = self.make_callback(answer)
            self.add_item(btn)

    def make_callback(self, answer):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.invoker_id:
                return await interaction.response.send_message("❌ This isn't your trivia question!", ephemeral=True)
            if self.answered:
                return await interaction.response.send_message("⏱️ Already answered!", ephemeral=True)
            self.answered = True
            self.stop()
            for item in self.children:
                item.disabled = True
                if item.label == self.correct: item.style = discord.ButtonStyle.success
                elif item.label == answer and answer != self.correct: item.style = discord.ButtonStyle.danger
            won = answer == self.correct
            embed = discord.Embed(
                description=f"✅ **Correct!** The answer was **{self.correct}**." if won else f"❌ **Wrong!** The correct answer was **{self.correct}**.",
                colour=discord.Colour.green() if won else discord.Colour.red())
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    async def on_timeout(self):
        if not self.answered:
            for item in self.children:
                item.disabled = True
                if item.label == self.correct: item.style = discord.ButtonStyle.success
            if self.message:
                try:
                    await self.message.edit(embed=discord.Embed(
                        description=f"⏱️ Time's up! The answer was **{self.correct}**.", colour=discord.Colour.orange()), view=self)
                except discord.NotFound: pass


async def fetch_trivia():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://opentdb.com/api.php?amount=1&type=multiple", timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return await resp.json()


def trivia_embed_and_view(data, invoker_id):
    result = data["results"][0]
    question = html.unescape(result["question"])
    correct  = html.unescape(result["correct_answer"])
    incorrect = [html.unescape(a) for a in result["incorrect_answers"]]
    diff = result["difficulty"]
    diff_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(diff, "⚪")
    colours = {"easy": discord.Colour.green(), "medium": discord.Colour.gold(), "hard": discord.Colour.red()}
    embed = discord.Embed(title="🧠  Trivia Question", description=f"**{question}**", colour=colours.get(diff, ACCENT))
    embed.add_field(name="Category",   value=html.unescape(result["category"]), inline=True)
    embed.add_field(name="Difficulty", value=f"{diff_emoji} {diff.capitalize()}",  inline=True)
    embed.set_footer(text="You have 20 seconds to answer!")
    view = TriviaView(correct, [correct] + incorrect, invoker_id)
    return embed, view


# ─── Main Cog ───────────────────────────────────────────────────
class Extras(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot
        self._reminder_tasks: dict[str, asyncio.Task] = {}
        self.afk_users = {}
        self.restore_reminders.start()

        self.save_extras.start()

    def cog_unload(self):
        self.save_extras.cancel()
        self.restore_reminders.cancel()
        for task in self._reminder_tasks.values(): task.cancel()
        
        if _extras_dirty_r:
            try:
                with open(REMINDERS_FILE, "w") as f:
                    json.dump(_reminders_cache, f, indent=2)
            except Exception:
                pass

    @tasks.loop(seconds=60)
    async def save_extras(self):
        global _extras_dirty_r
        dirty_r = False
        async with _extras_lock:
            if _extras_dirty_r:
                dirty_r = True
                _extras_dirty_r = False
        if dirty_r:
            def save_r():
                tmp = REMINDERS_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(_reminders_cache, f, indent=2)
                os.replace(tmp, REMINDERS_FILE)
            await asyncio.to_thread(save_r)

    @tasks.loop(count=1)
    async def restore_reminders(self):
        now = datetime.now(timezone.utc).timestamp()
        async with _extras_lock:
            data = list(_reminders_cache)
        for r in data:
            delay = r["fire_at"] - now
            if delay <= 0:
                self.bot.loop.create_task(self._fire_reminder(r, late=True))
            else:
                task = self.bot.loop.create_task(self._schedule_reminder(r, delay))
                self._reminder_tasks[r["id"]] = task

    @restore_reminders.before_loop
    async def _restore_reminders_before(self):
        await self.bot.wait_until_ready()

    async def _schedule_reminder(self, r: dict, delay: float):
        await asyncio.sleep(delay)
        await self._fire_reminder(r, late=False)

    async def _fire_reminder(self, r: dict, late: bool = False):
        await _remove_reminder(r["id"])
        self._reminder_tasks.pop(r["id"], None)
        channel = self.bot.get_channel(r["channel_id"])
        if channel is None:
            return
        user_mention = f"<@{r['user_id']}>"
        embed = discord.Embed(title="⏰  Reminder!", description=f"> {r['text']}", colour=ACCENT)
        if late:
            embed.set_footer(text="⚠️ This reminder was delayed because the bot was offline.")
        await channel.send(content=user_mention, embed=embed)



    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
            
        content = message.content
        if not content:
            if message.attachments:
                content = f"*[Attachment: {message.attachments[0].filename}]*"
            elif message.embeds:
                content = "*[Embedded Content]*"
                
        snipe_cache[message.channel.id] = {
            "content": content,
            "author": str(message.author),
            "avatar": message.author.display_avatar.url,
            "timestamp": message.created_at
        }

    def _snipe_embed(self, channel_id: int, channel_name: str):
        data = snipe_cache.get(channel_id)
        if not data:
            return discord.Embed(description="🔍 Nothing to snipe — the cache is empty.", colour=discord.Colour.orange()), False
        embed = discord.Embed(description=data["content"] or "*[no text content]*", colour=ACCENT, timestamp=data["timestamp"])
        embed.set_author(name=data["author"], icon_url=data["avatar"])
        embed.set_footer(text=f"Sniped in #{channel_name}")
        return embed, True

    @commands.command()
    @commands.guild_only()
    async def snipe(self, ctx: CustomContext):
        """ Show the last deleted message in this channel. """
        embed, _ = self._snipe_embed(ctx.channel.id, ctx.channel.name)
        await ctx.send(embed=embed)

    @app_commands.command(name="snipe", description="Show the last deleted message in this channel.")
    @app_commands.guild_only()
    async def slash_snipe(self, interaction: discord.Interaction):
        embed, _ = self._snipe_embed(interaction.channel_id, interaction.channel.name)
        await interaction.response.send_message(embed=embed)

    # ── Poll ───────────────────────────────────────────────────

    async def _create_poll(self, question: str, options: list, author: discord.Member, send_fn):
        number_emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
        if len(options) < 2:
            return None, "❌ Please provide at least **2 options**."
        if len(options) > 9:
            return None, "❌ Maximum **9 options** allowed."
        embed = discord.Embed(title=f"📊  {question}", colour=ACCENT)
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
        embed.description = "\n\n".join(f"{number_emojis[i]}  {opt}" for i, opt in enumerate(options))
        embed.set_footer(text="Vote by reacting below!")
        return embed, None

    @commands.command()
    @commands.guild_only()
    async def poll(self, ctx: CustomContext, question: str, *options: str):
        """ Create a reaction poll. Up to 9 options. Usage: !poll "Question" "Option 1" "Option 2" """
        embed, error = await self._create_poll(question, list(options), ctx.author, None)
        if error:
            return await ctx.send(embed=discord.Embed(description=error, colour=discord.Colour.red()))
        try: await ctx.message.delete()
        except discord.Forbidden: pass
        msg = await ctx.send(embed=embed)
        for i in range(len(options)):
            await msg.add_reaction(["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"][i])

    @app_commands.command(name="poll", description="Create a reaction poll with up to 9 options.")
    @app_commands.describe(question="Poll question", options="Options separated by commas or pipes (e.g. Yes, No, Maybe)")
    async def slash_poll(self, interaction: discord.Interaction, question: str, options: str):
        opts = [o.strip() for o in options.replace('|', ',').split(',') if o.strip()]
        embed, error = await self._create_poll(question, opts, interaction.user, None)
        if error:
            return await interaction.response.send_message(embed=discord.Embed(description=error, colour=discord.Colour.red()), ephemeral=True)
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        for i in range(len(opts)):
            await msg.add_reaction(["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"][i])

    # ── Remind Me ──────────────────────────────────────────────

    def _parse_time(self, time_str: str):
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit = time_str[-1].lower()
        if unit not in units or not time_str[:-1].isdigit():
            return None, "❌ Invalid time format. Use `10s`, `5m`, `2h`, or `1d`."
        seconds = int(time_str[:-1]) * units[unit]
        if seconds > 604800:
            return None, "❌ Maximum reminder time is **7 days**."
        return seconds, None

    async def _set_reminder(self, user_id: int, channel_id: int, guild_id: int, text: str, seconds: int) -> str:
        rid = str(uuid.uuid4())
        fire_at = datetime.now(timezone.utc).timestamp() + seconds
        await _add_reminder(rid, user_id, channel_id, guild_id, text, fire_at)
        r = {"id": rid, "user_id": user_id, "channel_id": channel_id,
             "guild_id": guild_id, "text": text, "fire_at": fire_at}
        task = self.bot.loop.create_task(self._schedule_reminder(r, seconds))
        self._reminder_tasks[rid] = task
        return rid

    @commands.command(aliases=["remind", "reminder"])
    async def remindme(self, ctx: CustomContext, time: str, *, reminder: str):
        """ Set a reminder. Format: 10s, 5m, 2h, 1d. Example: !remindme 30m do homework """
        seconds, error = self._parse_time(time)
        if error:
            return await ctx.send(embed=discord.Embed(description=error, colour=discord.Colour.red()))
        unix = int(datetime.now(timezone.utc).timestamp() + seconds)
        await self._set_reminder(ctx.author.id, ctx.channel.id, ctx.guild.id if ctx.guild else 0, reminder, seconds)
        embed = discord.Embed(title="⏰  Reminder Set", description=f"I'll remind you about:\n> {reminder}", colour=discord.Colour.green())
        embed.add_field(name="Fires", value=f"<t:{unix}:R>  (<t:{unix}:t>)")
        embed.set_footer(text="Reminder will be sent in this channel, even if the bot restarts.")
        await ctx.send(embed=embed)

    @app_commands.command(name="remindme", description="Set a reminder. Format: 10s, 5m, 2h, 1d.")
    @app_commands.describe(time="Time until reminder (e.g. 30m, 2h, 1d)", reminder="What to remind you about")
    async def slash_remindme(self, interaction: discord.Interaction, time: str, reminder: str):
        seconds, error = self._parse_time(time)
        if error:
            return await interaction.response.send_message(embed=discord.Embed(description=error, colour=discord.Colour.red()), ephemeral=True)
        unix = int(datetime.now(timezone.utc).timestamp() + seconds)
        await self._set_reminder(interaction.user.id, interaction.channel.id,
                           interaction.guild.id if interaction.guild else 0, reminder, seconds)
        embed = discord.Embed(title="⏰  Reminder Set", description=f"I'll remind you about:\n> {reminder}", colour=discord.Colour.green())
        embed.add_field(name="Fires", value=f"<t:{unix}:R>  (<t:{unix}:t>)")
        embed.set_footer(text="Reminder will be sent in this channel, even if the bot restarts.")
        await interaction.response.send_message(embed=embed)

    # ── List Reminders ─────────────────────────────────────────

    @commands.command(aliases=["myreminders", "rlist"])
    async def reminders(self, ctx: CustomContext):
        """ Show all your active reminders. """
        await ctx.send(embed=self._reminders_embed(ctx.author))

    @app_commands.command(name="reminders", description="Show all your active reminders.")
    async def slash_reminders(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._reminders_embed(interaction.user), ephemeral=True)

    def _reminders_embed(self, user: discord.User | discord.Member) -> discord.Embed:
        all_r = list(_reminders_cache)
        mine = [r for r in all_r if r["user_id"] == user.id]
        embed = discord.Embed(title=f"⏰ Your Reminders", colour=ACCENT)
        embed.set_thumbnail(url=user.display_avatar.url)
        if not mine:
            embed.description = "You have no active reminders.\nUse `!remindme <time> <text>` to set one."
            return embed
        mine.sort(key=lambda r: r["fire_at"])
        lines = []
        for i, r in enumerate(mine, 1):
            ts = int(r["fire_at"])
            ch = f"<#{r['channel_id']}>"
            lines.append(f"**{i}.** <t:{ts}:R> — {r['text'][:80]}\n└ {ch} · <t:{ts}:f>")
        embed.description = "\n\n".join(lines)
        embed.set_footer(text=f"{len(mine)} active reminder{'s' if len(mine) != 1 else ''}")
        return embed

    # ── Tic Tac Toe ────────────────────────────────────────────

    @commands.command(aliases=["ttt"])
    @commands.guild_only()
    async def tictactoe(self, ctx: CustomContext, opponent: discord.Member):
        """ Challenge someone to Tic Tac Toe! """
        if opponent.id == ctx.author.id:
            return await ctx.send(embed=discord.Embed(description="❌ You can't play against yourself!", colour=discord.Colour.red()))
        if opponent.bot:
            return await ctx.send(embed=discord.Embed(description="❌ You can't play against a bot.", colour=discord.Colour.red()))
        embed = discord.Embed(title="🎮  Tic Tac Toe",
            description=f"**{ctx.author.display_name}** (❌) vs **{opponent.display_name}** (⭕)\n\nIt's **{ctx.author.display_name}**'s turn (❌)", colour=ACCENT)
        view = TTTView(ctx.author, opponent)
        view.message = await ctx.send(embed=embed, view=view)

    @app_commands.command(name="tictactoe", description="Challenge someone to Tic Tac Toe!")
    @app_commands.describe(opponent="Who to challenge")
    async def slash_tictactoe(self, interaction: discord.Interaction, opponent: discord.Member):
        if opponent.id == interaction.user.id:
            return await interaction.response.send_message(embed=discord.Embed(description="❌ You can't play against yourself!", colour=discord.Colour.red()), ephemeral=True)
        if opponent.bot:
            return await interaction.response.send_message(embed=discord.Embed(description="❌ You can't play against a bot.", colour=discord.Colour.red()), ephemeral=True)
        embed = discord.Embed(title="🎮  Tic Tac Toe",
            description=f"**{interaction.user.display_name}** (❌) vs **{opponent.display_name}** (⭕)\n\nIt's **{interaction.user.display_name}**'s turn (❌)", colour=ACCENT)
        view = TTTView(interaction.user, opponent)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    # ── Trivia ─────────────────────────────────────────────────

    @commands.command()
    @commands.cooldown(rate=1, per=5.0, type=commands.BucketType.user)
    async def trivia(self, ctx: CustomContext):
        """ Answer a random trivia question! """
        async with ctx.channel.typing():
            try:
                data = await fetch_trivia()
            except Exception:
                return await ctx.send(embed=discord.Embed(description="❌ Could not reach the trivia API.", colour=discord.Colour.red()))
        embed, view = trivia_embed_and_view(data, ctx.author.id)
        view.message = await ctx.send(embed=embed, view=view)

    @app_commands.command(name="trivia", description="Answer a random trivia question!")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def slash_trivia(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            data = await fetch_trivia()
        except Exception:
            return await interaction.followup.send(embed=discord.Embed(description="❌ Could not reach the trivia API.", colour=discord.Colour.red()))
        embed, view = trivia_embed_and_view(data, interaction.user.id)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg

    # ── Autorole listener ──────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        autorole_id = getattr(self.bot.config, "discord_autorole_id", None)
        if not autorole_id:
            return
        role = member.guild.get_role(autorole_id)
        if role:
            try:
                await member.add_roles(role, reason="Auto-role on join")
            except discord.Forbidden:
                pass



    @commands.hybrid_command(name="choose", description="Randomly choose from a list of options.")
    @app_commands.describe(options="Options separated by commas")
    async def choose(self, ctx: CustomContext, *, options: str):
        opts = [o.strip() for o in options.split(',') if o.strip()]
        if len(opts) < 2:
            return await ctx.send("❌ Please provide at least two options separated by commas.", ephemeral=True)
        choice = random.choice(opts)
        embed = discord.Embed(description=f"🤔 I choose: **{choice}**", colour=ACCENT)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="afk", description="Set your AFK status.")
    @app_commands.describe(message="The AFK message")
    async def afk(self, ctx: CustomContext, *, message: str = "AFK"):
        guild_id = ctx.guild.id
        if guild_id not in self.afk_users:
            self.afk_users[guild_id] = {}
            
        # Delete the user's !afk message if it's a prefix command
        if not ctx.interaction:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
            
        # Explicitly save the actual nick. 
        original_nick = ctx.author.nick
        if ctx.author.id in self.afk_users[guild_id]:
            original_nick = self.afk_users[guild_id][ctx.author.id][2]
        
        self.afk_users[guild_id][ctx.author.id] = (message, datetime.now(timezone.utc), original_nick)
        
        try:
            if not ctx.author.display_name.startswith("[AFK]"):
                new_nick = f"[AFK] {ctx.author.display_name}"
                if len(new_nick) > 32:
                    new_nick = f"[AFK] {ctx.author.display_name[:25]}\u2026"
                await ctx.author.edit(nick=new_nick)
        except discord.Forbidden:
            pass
            
        # Removed the mention and added a 10-second auto-delete
        await ctx.send(embed=discord.Embed(description=f"✅ I set your AFK: **{message}**", colour=discord.Colour.red()), delete_after=10.0)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
            
        guild_id = message.guild.id
        
        # Clear AFK
        if guild_id in self.afk_users and message.author.id in self.afk_users[guild_id]:
            afk_data = self.afk_users[guild_id][message.author.id]
            
            # Race condition fix: Don't clear AFK if it was set less than 3 seconds ago
            if (datetime.now(timezone.utc) - afk_data[1]).total_seconds() > 3.0:
                self.afk_users[guild_id].pop(message.author.id)
                original_nick = afk_data[2] if len(afk_data) > 2 else None
                
                welcome_embed = discord.Embed(description=f"👋 Welcome back {message.author.mention}, I removed your AFK.", colour=discord.Colour.green())
                await message.channel.send(embed=welcome_embed, delete_after=7.0)
                
                try:
                    name = message.author.display_name
                    if name.startswith("[AFK]"):
                        await message.author.edit(nick=original_nick)
                except discord.Forbidden:
                    pass
                
        # Check mentions
        if guild_id in self.afk_users:
            notified = set()
            for mention in message.mentions:
                # Stop duplicate message spam if a user is tagged 5 times 
                if mention.id in self.afk_users[guild_id] and mention.id not in notified:
                    afk_data = self.afk_users[guild_id][mention.id]
                    afk_msg = afk_data[0]
                    afk_time = afk_data[1]
                    ts = int(afk_time.timestamp())
                    
                    # Added a 10-second auto-delete so it doesn't clog the chat
                    await message.channel.send(embed=discord.Embed(description=f"💤 **{mention.display_name}** is AFK: {afk_msg} (<t:{ts}:R>)", colour=discord.Colour.orange()), delete_after=10.0)
                    notified.add(mention.id)


async def setup(bot):
    await bot.add_cog(Extras(bot))