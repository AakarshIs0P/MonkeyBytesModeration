# blacklist.py

import discord
from discord.ext import commands


class Blacklist(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # In-memory blacklist
        # Replace with DB/json later if wanted
        self.blacklisted_users = set()

    async def cog_check(self, ctx):
        """
        Blocks blacklisted users from using commands
        """
        if ctx.author.id in self.blacklisted_users:
            raise commands.CheckFailure(
                "You are blacklisted from using this bot."
            )

        return True

    @commands.command()
    @commands.is_owner()
    async def blacklist(self, ctx, user: discord.User):
        """
        Blacklist a user from using bot commands
        """

        if user.id in self.blacklisted_users:
            return await ctx.send(
                f"{user} is already blacklisted."
            )

        self.blacklisted_users.add(user.id)

        embed = discord.Embed(
            title="User Blacklisted",
            description=f"{user.mention} has been blacklisted.",
            color=discord.Color.red()
        )

        await ctx.send(embed=embed)

    @commands.command()
    @commands.is_owner()
    async def unblacklist(self, ctx, user: discord.User):
        """
        Remove user from blacklist
        """

        if user.id not in self.blacklisted_users:
            return await ctx.send(
                f"{user} is not blacklisted."
            )

        self.blacklisted_users.remove(user.id)

        embed = discord.Embed(
            title="User Unblacklisted",
            description=f"{user.mention} has been unblacklisted.",
            color=discord.Color.green()
        )

        await ctx.send(embed=embed)

    @commands.command()
    @commands.is_owner()
    async def blacklistlist(self, ctx):
        """
        Show all blacklisted users
        """

        if not self.blacklisted_users:
            return await ctx.send("Blacklist is empty.")

        users = []

        for user_id in self.blacklisted_users:
            user = self.bot.get_user(user_id)

            if user:
                users.append(f"• {user} ({user_id})")
            else:
                users.append(f"• Unknown User ({user_id})")

        embed = discord.Embed(
            title="Blacklisted Users",
            description="\n".join(users),
            color=discord.Color.orange()
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Blacklist(bot))