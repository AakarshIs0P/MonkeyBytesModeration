import random
import discord
import secrets
import asyncio
import aiohttp

from io import BytesIO
from utils.default import CustomContext
from discord.ext import commands
from discord import app_commands
from utils import permissions, http
from utils.data import DiscordBot

ACCENT = discord.Colour.from_str("#5865F2")


class Fun_Commands(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot

    # ── 8ball ──────────────────────────────────────────────────────────

    async def _eightball_logic(self, question: str, author: discord.Member) -> discord.Embed:
        positive = ["Yes, absolutely!", "Without a doubt.", "Most likely.", "Sure thing!", "It is certain.", "Signs point to yes."]
        neutral  = ["Ask again later.", "Hard to say right now.", "You'll be the judge.", "The stars are unclear...", "Better not tell you now."]
        negative = ["Very doubtful.", "Don't count on it.", "My sources say no.", "Outlook not so good.", "No way."]
        all_r = positive + neutral + negative
        answer = random.choice(all_r)
        colour = discord.Colour.green() if answer in positive else (discord.Colour.gold() if answer in neutral else discord.Colour.red())
        emoji  = "✅" if answer in positive else ("🤔" if answer in neutral else "❌")
        embed = discord.Embed(colour=colour)
        embed.set_author(name="Magic 8-Ball 🎱", icon_url=author.display_avatar.url)
        embed.add_field(name="❓ Question", value=question, inline=False)
        embed.add_field(name=f"{emoji} Answer", value=answer, inline=False)
        return embed

    @commands.command(name="eightball", aliases=["8ball"])
    async def eightball(self, ctx: CustomContext, *, question: commands.clean_content):
        """ Ask the magic 8-ball a question. """
        await ctx.send(embed=await self._eightball_logic(question, ctx.author))

    @app_commands.command(name="8ball", description="Ask the magic 8-ball a question.")
    @app_commands.describe(question="The question to ask")
    async def slash_eightball(self, interaction: discord.Interaction, question: str):
        await interaction.response.send_message(embed=await self._eightball_logic(question, interaction.user))

    # ── Random image helpers ───────────────────────────────────────────

    async def _fetch_image(self, url: str, *keys) -> str | None:
        try:
            r = await http.get(url, res_method="json")
            result = r.response
            for k in keys:
                result = result[k]
            return result
        except Exception:
            return None

    # ── Duck ──────────────────────────────────────────────────────────

    @commands.command()
    @commands.cooldown(1, 1.5, commands.BucketType.user)
    async def duck(self, ctx: CustomContext):
        """ Posts a random duck 🦆 """
        url = await self._fetch_image("https://random-d.uk/api/v1/random", "url")
        await ctx.send(url or "❌ API seems down.")

    # ── Coffee ────────────────────────────────────────────────────────

    @commands.command()
    @commands.cooldown(1, 1.5, commands.BucketType.user)
    async def coffee(self, ctx: CustomContext):
        """ Posts a random coffee ☕ """
        url = await self._fetch_image("https://coffee.alexflipnote.dev/random.json", "file")
        await ctx.send(url or "❌ API seems down.")

    # ── Cat ───────────────────────────────────────────────────────────

    @commands.command()
    @commands.cooldown(1, 1.5, commands.BucketType.user)
    async def cat(self, ctx: CustomContext):
        """ Posts a random cat 🐱 """
        url = await self._fetch_image("https://api.alexflipnote.dev/cats", "file")
        await ctx.send(url or "❌ API seems down.")

    @app_commands.command(name="cat", description="Posts a random cat 🐱")
    async def slash_cat(self, interaction: discord.Interaction):
        url = await self._fetch_image("https://api.alexflipnote.dev/cats", "file")
        await interaction.response.send_message(url or "❌ API seems down.")

    # ── Dog ───────────────────────────────────────────────────────────

    @commands.command()
    @commands.cooldown(1, 1.5, commands.BucketType.user)
    async def dog(self, ctx: CustomContext):
        """ Posts a random dog 🐶 """
        url = await self._fetch_image("https://api.alexflipnote.dev/dogs", "file")
        await ctx.send(url or "❌ API seems down.")

    @app_commands.command(name="dog", description="Posts a random dog 🐶")
    async def slash_dog(self, interaction: discord.Interaction):
        url = await self._fetch_image("https://api.alexflipnote.dev/dogs", "file")
        await interaction.response.send_message(url or "❌ API seems down.")

    # ── Coinflip ──────────────────────────────────────────────────────

    async def _coinflip_embed(self, author_name: str) -> discord.Embed:
        result = random.choice(["Heads", "Tails"])
        emoji  = "🌕" if result == "Heads" else "🌑"
        return discord.Embed(title=f"{emoji} Coin Flip",
            description=f"**{author_name}** flipped a coin and got **{result}**!",
            colour=discord.Colour.gold())

    @commands.command(aliases=["flip", "coin"])
    async def coinflip(self, ctx: CustomContext):
        """ Flip a coin! """
        await ctx.send(embed=await self._coinflip_embed(ctx.author.display_name))

    @app_commands.command(name="coinflip", description="Flip a coin!")
    async def slash_coinflip(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=await self._coinflip_embed(interaction.user.display_name))

    # ── F ─────────────────────────────────────────────────────────────

    async def _f_embed(self, author: discord.Member, text: str = None) -> discord.Embed:
        hearts = ["❤️","💛","💚","💙","💜"]
        reason = f"for **{text}** " if text else ""
        embed = discord.Embed(description=f"**{author.display_name}** has paid their respects {reason}{random.choice(hearts)}", colour=ACCENT)
        embed.set_footer(text="F")
        return embed

    @commands.command()
    async def f(self, ctx: CustomContext, *, text: commands.clean_content = None):
        """ Press F to pay respects. """
        await ctx.send(embed=await self._f_embed(ctx.author, text))

    # ── Urban ─────────────────────────────────────────────────────────

    async def _urban_embed(self, search: str) -> discord.Embed:
        try:
            r = await http.get(f"https://api.urbandictionary.com/v0/define?term={search}", res_method="json")
        except Exception:
            return discord.Embed(description="❌ Urban Dictionary API is unavailable.", colour=discord.Colour.red())
        if not r.response or not r.response["list"]:
            return discord.Embed(description=f"❌ No definition found for **{search}**.", colour=discord.Colour.red())
        result = sorted(r.response["list"], reverse=True, key=lambda g: int(g["thumbs_up"]))[0]
        definition = result["definition"]
        if len(definition) >= 1000:
            definition = definition[:1000].rsplit(" ", 1)[0] + "..."
        embed = discord.Embed(title=f"📚 {result['word']}", description=definition, colour=ACCENT)
        embed.set_footer(text=f"👍 {result['thumbs_up']}  👎 {result['thumbs_down']}")
        return embed

    @commands.command()
    @commands.cooldown(1, 2.0, commands.BucketType.user)
    async def urban(self, ctx: CustomContext, *, search: commands.clean_content):
        """ Look up a word on Urban Dictionary. """
        async with ctx.channel.typing():
            await ctx.send(embed=await self._urban_embed(search))

    @app_commands.command(name="urban", description="Look up a word on Urban Dictionary.")
    @app_commands.describe(search="Word or phrase to look up")
    async def slash_urban(self, interaction: discord.Interaction, search: str):
        await interaction.response.defer()
        await interaction.followup.send(embed=await self._urban_embed(search))

    # ── Reverse ───────────────────────────────────────────────────────

    async def _reverse_embed(self, text: str) -> discord.Embed:
        t_rev = text[::-1].replace("@","@\u200B").replace("&","&\u200B")
        return discord.Embed(title="🔁 Reversed", description=t_rev, colour=ACCENT)

    @commands.command()
    async def reverse(self, ctx: CustomContext, *, text: str):
        """ Reverse any text. """
        await ctx.send(embed=await self._reverse_embed(text), allowed_mentions=discord.AllowedMentions.none())

    # ── Password ──────────────────────────────────────────────────────

    async def _send_password(self, nbytes: int, user: discord.Member, channel_send=None):
        if nbytes not in range(3, 1401):
            err = discord.Embed(description="❌ Number must be between **3** and **1400**.", colour=discord.Colour.red())
            return err, None
        pw_embed = discord.Embed(title="🔐 Your Generated Password",
            description=f"```{secrets.token_urlsafe(nbytes)}```", colour=discord.Colour.green())
        pw_embed.set_footer(text="Keep this safe — don't share it with anyone.")
        return None, pw_embed

    @commands.command()
    async def password(self, ctx: CustomContext, nbytes: int = 18):
        """ Generate a secure random password (3–1400 bytes). """
        err, pw = await self._send_password(nbytes, ctx.author)
        if err:
            return await ctx.send(embed=err)
        if ctx.guild:
            await ctx.send(embed=discord.Embed(description=f"📬 {ctx.author.mention}, sent you a DM with your password!", colour=discord.Colour.green()))
        await ctx.author.send(embed=pw)

    @app_commands.command(name="password", description="Generate a secure random password sent to your DMs.")
    @app_commands.describe(nbytes="Length in bytes (3–1400, default 18)")
    async def slash_password(self, interaction: discord.Interaction, nbytes: int = 18):
        err, pw = await self._send_password(nbytes, interaction.user)
        if err:
            return await interaction.response.send_message(embed=err, ephemeral=True)
        await interaction.response.send_message(embed=discord.Embed(description="📬 Password sent to your DMs!", colour=discord.Colour.green()), ephemeral=True)
        await interaction.user.send(embed=pw)

    # ── Rate ──────────────────────────────────────────────────────────

    async def _rate_embed(self, thing: str) -> discord.Embed:
        score = round(random.uniform(0.0, 100.0), 2)
        if score >= 75:   colour, verdict = discord.Colour.green(),  "Excellent! 🌟"
        elif score >= 50: colour, verdict = discord.Colour.gold(),   "Pretty decent 👍"
        elif score >= 25: colour, verdict = discord.Colour.orange(), "Could be better 🤷"
        else:             colour, verdict = discord.Colour.red(),    "Yikes... 💀"
        embed = discord.Embed(title=f"⭐ Rating: {thing}", colour=colour)
        embed.add_field(name="Score",   value=f"**{score} / 100**")
        embed.add_field(name="Verdict", value=verdict)
        return embed

    @commands.command()
    async def rate(self, ctx: CustomContext, *, thing: commands.clean_content):
        """ Rate anything out of 100. """
        await ctx.send(embed=await self._rate_embed(thing))

    @app_commands.command(name="rate", description="Rate anything out of 100.")
    @app_commands.describe(thing="What to rate")
    async def slash_rate(self, interaction: discord.Interaction, thing: str):
        await interaction.response.send_message(embed=await self._rate_embed(thing))

    # ── Beer ──────────────────────────────────────────────────────────

    @commands.command()
    async def beer(self, ctx: CustomContext, user: discord.Member = None, *, reason: commands.clean_content = ""):
        """ Offer someone a beer! 🍺 """
        if not user or user.id == ctx.author.id:
            return await ctx.send(f"**{ctx.author.name}**: paaaarty! 🎉🍺")
        if user.id == self.bot.user.id:
            return await ctx.send("*clinks glass with you* 🍻")
        if user.bot:
            return await ctx.send(f"**{user.name}** is a bot — bots don't drink 🤖")
        msg_text = f"**{user.mention}**, you have a 🍺 offer from **{ctx.author.name}**!"
        if reason: msg_text += f"\n> {reason}"
        msg = await ctx.send(msg_text)
        def check(m): return m.message_id == msg.id and m.user_id == user.id and str(m.emoji) == "🍻"
        try:
            await msg.add_reaction("🍻")
            await self.bot.wait_for("raw_reaction_add", timeout=30.0, check=check)
            await msg.edit(content=f"🍻 **{user.name}** and **{ctx.author.name}** are enjoying a beer together!")
        except asyncio.TimeoutError:
            await msg.delete()
            await ctx.send(f"😔 **{user.name}** didn't want a beer with **{ctx.author.name}**...")
        except discord.Forbidden:
            await msg.edit(content=msg_text)

    # ── Hotcalc ───────────────────────────────────────────────────────

    async def _hotcalc_embed(self, user: discord.Member) -> discord.Embed:
        rng = random.Random(user.id)
        hot = rng.randint(1, 100) / 1.17
        if hot > 75:   emoji, colour, verdict = "💞", discord.Colour.red(),    "On fire! 🔥"
        elif hot > 50: emoji, colour, verdict = "💖", discord.Colour.magenta(),"Pretty hot!"
        elif hot > 25: emoji, colour, verdict = "❤️", discord.Colour.orange(), "Warm vibes"
        else:          emoji, colour, verdict = "💔", discord.Colour.blue(),   "Ice cold 🧊"
        embed = discord.Embed(title=f"{emoji} Hot Calc — {user.display_name}",
            description=f"**{hot:.2f}%** hot — {verdict}", colour=colour)
        embed.set_thumbnail(url=user.display_avatar.url)
        return embed

    @commands.command(aliases=["howhot","hot"])
    async def hotcalc(self, ctx: CustomContext, *, user: discord.Member = None):
        """ How hot is someone? 🔥 """
        await ctx.send(embed=await self._hotcalc_embed(user or ctx.author))

    # ── Slot ──────────────────────────────────────────────────────────

    async def _slot_embed(self, author: discord.Member) -> discord.Embed:
        a, b, c = [random.choice("🍎🍊🍐🍋🍉🍇🍓🍒") for _ in range(3)]
        if a == b == c:   result, colour = "🎉 **Jackpot! All three match!**", discord.Colour.gold()
        elif a==b or a==c or b==c: result, colour = "✨ **2 in a row! You win!**", discord.Colour.green()
        else:             result, colour = "💸 **No match. Better luck next time!**", discord.Colour.red()
        embed = discord.Embed(title="🎰 Slot Machine", colour=colour)
        embed.add_field(name="Result",  value=f"**[ {a}  {b}  {c} ]**", inline=False)
        embed.add_field(name="Outcome", value=result,                   inline=False)
        embed.set_footer(text=author.display_name, icon_url=author.display_avatar.url)
        return embed

    @commands.command(aliases=["slots","bet"])
    async def slot(self, ctx: CustomContext):
        """ Roll the slot machine 🎰 """
        await ctx.send(embed=await self._slot_embed(ctx.author))

    # ── Dice ──────────────────────────────────────────────────────────

    async def _dice_embed(self, bot_name: str, author: discord.Member) -> discord.Embed:
        bot_dice, player_dice = random.randint(1,6), random.randint(1,6)
        if player_dice > bot_dice:   msg, colour = "🎉 You win!",    discord.Colour.green()
        elif player_dice < bot_dice: msg, colour = "💀 You lost!",   discord.Colour.red()
        else:                        msg, colour = "🤝 It's a tie!", discord.Colour.gold()
        embed = discord.Embed(title="🎲 Dice Roll", colour=colour)
        embed.add_field(name=f"🤖 {bot_name}",           value=f"Rolled **{bot_dice}**")
        embed.add_field(name=f"👤 {author.display_name}", value=f"Rolled **{player_dice}**")
        embed.add_field(name="Result", value=msg, inline=False)
        return embed

    @commands.command()
    async def dice(self, ctx: CustomContext):
        """ Roll a dice against the bot. 🎲 """
        await ctx.send(embed=await self._dice_embed(self.bot.user.display_name, ctx.author))

    @app_commands.command(name="dice", description="Roll a dice against the bot 🎲")
    async def slash_dice(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=await self._dice_embed(interaction.client.user.display_name, interaction.user))

    # ── Roulette ──────────────────────────────────────────────────────

    @commands.command(aliases=["roul"])
    async def roulette(self, ctx: CustomContext, picked_colour: str = None):
        """ Colour roulette — pick a colour and try your luck! """
        colour_table = ["blue","red","green","yellow"]
        emojis = {"blue":"🔵","red":"🔴","green":"🟢","yellow":"🟡"}
        if not picked_colour:
            return await ctx.send(f"🎡 Pick: {' '.join(f'{emojis[c]} `{c}`' for c in colour_table)}")
        picked_colour = picked_colour.lower()
        if picked_colour not in colour_table:
            return await ctx.send("❌ Invalid colour. Choose: blue, red, green, or yellow.")
        msg_embed = discord.Embed(title="🎡 Roulette — Spinning...", description="🔵🔴🟢🟡", colour=ACCENT)
        msg = await ctx.send(embed=msg_embed)
        await asyncio.sleep(2)
        chosen = random.choice(colour_table)
        won = chosen == picked_colour
        result_embed = discord.Embed(title="🎡 Roulette Result", colour=discord.Colour.green() if won else discord.Colour.red())
        result_embed.add_field(name="You Picked", value=f"{emojis[picked_colour]} {picked_colour.capitalize()}")
        result_embed.add_field(name="Result",     value=f"{emojis[chosen]} {chosen.capitalize()}")
        result_embed.add_field(name="Outcome",    value="🎉 **You won!**" if won else "💸 **Better luck next time!**", inline=False)
        await msg.edit(embed=result_embed)

    @app_commands.command(name="roulette", description="Colour roulette — pick a colour and try your luck!")
    @app_commands.describe(colour="Choose: blue, red, green, or yellow")
    @app_commands.choices(colour=[
        app_commands.Choice(name="🔵 Blue",   value="blue"),
        app_commands.Choice(name="🔴 Red",    value="red"),
        app_commands.Choice(name="🟢 Green",  value="green"),
        app_commands.Choice(name="🟡 Yellow", value="yellow"),
    ])
    async def slash_roulette(self, interaction: discord.Interaction, colour: str):
        emojis = {"blue":"🔵","red":"🔴","green":"🟢","yellow":"🟡"}
        colour_table = list(emojis.keys())
        await interaction.response.send_message("🎡 Spinning the wheel...", ephemeral=False)
        await asyncio.sleep(2)
        chosen = random.choice(colour_table)
        won = chosen == colour
        result_embed = discord.Embed(title="🎡 Roulette Result", colour=discord.Colour.green() if won else discord.Colour.red())
        result_embed.add_field(name="You Picked", value=f"{emojis[colour]} {colour.capitalize()}")
        result_embed.add_field(name="Result",     value=f"{emojis[chosen]} {chosen.capitalize()}")
        result_embed.add_field(name="Outcome",    value="🎉 **You won!**" if won else "💸 **Better luck next time!**", inline=False)
        await interaction.edit_original_response(content=None, embed=result_embed)

    # ── Random Fact ───────────────────────────────────────────────────

    FACTS = [
        "Honey never spoils — edible honey has been found in 3000-year-old Egyptian tombs.",
        "A day on Venus is longer than a year on Venus.",
        "Octopuses have three hearts and blue blood.",
        "Bananas are technically berries, but strawberries are not.",
        "A group of flamingos is called a 'flamboyance'.",
        "Sloths can hold their breath longer than dolphins by slowing their heart rate.",
        "Some turtles can breathe through their butts.",
        "The Eiffel Tower can grow up to 6 inches taller in summer due to heat expansion.",
        "Crows can recognize human faces and hold grudges.",
        "A bolt of lightning is five times hotter than the surface of the sun.",
    ]

    async def _fact_embed(self) -> discord.Embed:
        embed = discord.Embed(title="🧠 Random Fact", description=random.choice(self.FACTS), colour=ACCENT)
        embed.set_footer(text="The more you know!")
        return embed

    @commands.command()
    async def randomfact(self, ctx: CustomContext):
        """ Get a random fun fact. 🧠 """
        await ctx.send(embed=await self._fact_embed())

    @app_commands.command(name="randomfact", description="Get a random fun fact 🧠")
    async def slash_randomfact(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=await self._fact_embed())


async def setup(bot):
    await bot.add_cog(Fun_Commands(bot))
