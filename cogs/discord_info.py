import discord

from io import BytesIO
from utils import default
from utils.default import CustomContext
from discord.ext import commands
from discord import app_commands
from utils.data import DiscordBot
from cogs.msg_stats import get_msg_count, get_guild_total


class Discord_Info(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot

    # ── Avatar ────────────────────────────────────────────────────────

    def _avatar_embed(self, user: discord.Member) -> discord.Embed:
        avatars_list = []
        def fmt(target):
            fmts = ["JPEG", "PNG", "WebP"]
            if target.is_animated():
                fmts.append("GIF")
            return fmts

        if not user.avatar and not user.guild_avatar:
            return discord.Embed(description=f"**{user}** has no avatar set.", colour=discord.Colour.red())

        embed = discord.Embed(title=f"🖼️ {user.display_name}'s Avatar",
            colour=user.top_role.colour if user.top_role.colour.value else discord.Colour.blurple())

        if user.avatar:
            avatars_list.append("**Account Avatar:** " + " **·** ".join(
                f"[{f}]({user.avatar.replace(format=f.lower(), size=1024)})" for f in fmt(user.avatar)))

        if user.guild_avatar:
            avatars_list.append("**Server Avatar:** " + " **·** ".join(
                f"[{f}]({user.guild_avatar.replace(format=f.lower(), size=1024)})" for f in fmt(user.guild_avatar)))
            embed.set_thumbnail(url=user.avatar.replace(format="png"))

        embed.set_image(url=str(user.display_avatar.with_size(256).with_static_format("png")))
        embed.description = "\n".join(avatars_list)
        return embed

    @commands.command(aliases=["av", "pfp"])
    @commands.guild_only()
    async def avatar(self, ctx: CustomContext, *, user: discord.Member = None):
        """ Get someone's avatar. """
        await ctx.send(embed=self._avatar_embed(user or ctx.author))

    @app_commands.command(name="avatar", description="Get someone's avatar.")
    @app_commands.describe(user="Whose avatar to show (default: yours)")
    async def slash_avatar(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.send_message(embed=self._avatar_embed(user or interaction.user))

    # ── Roles ─────────────────────────────────────────────────────────

    @commands.command()
    @commands.guild_only()
    async def roles(self, ctx: CustomContext):
        """ List all roles in this server. """
        allroles = ""
        for num, role in enumerate(sorted(ctx.guild.roles, reverse=True), start=1):
            allroles += f"[{str(num).zfill(2)}] {role.id}\t{role.name}\t[ Users: {len(role.members)} ]\r\n"
        data = BytesIO(allroles.encode("utf-8"))
        await ctx.send(content=f"📋 Roles in **{ctx.guild.name}**",
            file=discord.File(data, filename=f"{default.timetext('Roles')}"))

    @app_commands.command(name="roles", description="List all roles in this server.")
    async def slash_roles(self, interaction: discord.Interaction):
        allroles = ""
        for num, role in enumerate(sorted(interaction.guild.roles, reverse=True), start=1):
            allroles += f"[{str(num).zfill(2)}] {role.id}\t{role.name}\t[ Users: {len(role.members)} ]\r\n"
        data = BytesIO(allroles.encode("utf-8"))
        await interaction.response.send_message(content=f"📋 Roles in **{interaction.guild.name}**",
            file=discord.File(data, filename=f"{default.timetext('Roles')}"))

    # ── Joined At ─────────────────────────────────────────────────────

    def _joinedat_embed(self, user: discord.Member, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(title=f"📅 Join Date — {user.display_name}",
            colour=user.top_role.colour if user.top_role.colour.value else discord.Colour.blurple())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Server", value=guild.name)
        embed.add_field(name="Joined", value=default.date(user.joined_at, ago=True))
        return embed

    @commands.command(aliases=["joindate", "joined"])
    @commands.guild_only()
    async def joinedat(self, ctx: CustomContext, *, user: discord.Member = None):
        """ Check when a user joined this server. """
        await ctx.send(embed=self._joinedat_embed(user or ctx.author, ctx.guild))

    @app_commands.command(name="joinedat", description="Check when a user joined this server.")
    @app_commands.describe(user="The user to check (default: yourself)")
    async def slash_joinedat(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.send_message(embed=self._joinedat_embed(user or interaction.user, interaction.guild))

    # ── Mods ──────────────────────────────────────────────────────────

    def _mods_embed(self, guild: discord.Guild, channel: discord.TextChannel) -> discord.Embed:
        all_status = {
            "online":  {"users": [], "emoji": "🟢"},
            "idle":    {"users": [], "emoji": "🟡"},
            "dnd":     {"users": [], "emoji": "🔴"},
            "offline": {"users": [], "emoji": "⚫"},
        }
        for user in guild.members:
            perms = channel.permissions_for(user)
            if (perms.kick_members or perms.ban_members) and not user.bot:
                all_status[str(user.status)]["users"].append(f"**{user}**")
        embed = discord.Embed(title=f"🛡️ Moderators — {guild.name}", colour=discord.Colour.blurple())
        for status, info in all_status.items():
            if info["users"]:
                embed.add_field(name=f"{info['emoji']} {status.capitalize()}", value=", ".join(info["users"]), inline=False)
        return embed

    @commands.command()
    @commands.guild_only()
    async def mods(self, ctx: CustomContext):
        """ Check which moderators are online. """
        await ctx.send(embed=self._mods_embed(ctx.guild, ctx.channel))

    @app_commands.command(name="mods", description="Check which moderators are online.")
    async def slash_mods(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._mods_embed(interaction.guild, interaction.channel))

    # ── Server ────────────────────────────────────────────────────────

    def _server_embed(self, guild: discord.Guild) -> discord.Embed:
        find_bots = sum(1 for m in guild.members if m.bot)
        total_msgs = get_guild_total(guild.id)
        embed = discord.Embed(title=f"🏠 {guild.name}", colour=discord.Colour.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.with_format("png").with_size(1024))
        embed.add_field(name="🆔 Server ID",       value=guild.id)
        embed.add_field(name="👥 Members",          value=guild.member_count)
        embed.add_field(name="🤖 Bots",             value=find_bots)
        embed.add_field(name="👑 Owner",            value=str(guild.owner))
        embed.add_field(name="📅 Created",          value=default.date(guild.created_at, ago=True))
        embed.add_field(name="💬 Total Messages",   value=f"{total_msgs:,}" if total_msgs else "Not tracked yet")
        embed.add_field(name="💎 Boosts",           value=f"Level {guild.premium_tier} ({guild.premium_subscription_count} boosts)")
        embed.add_field(name="🛡️ Verification",     value=str(guild.verification_level).capitalize())
        embed.add_field(name="👻 Emoji Count",      value=f"{len(guild.emojis)} / {guild.emoji_limit}")
        return embed

    @commands.group()
    @commands.guild_only()
    async def server(self, ctx: CustomContext):
        """ Server information. """
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=self._server_embed(ctx.guild))

    @app_commands.command(name="server", description="Show information about this server.")
    async def slash_server(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._server_embed(interaction.guild))

    @server.command(name="avatar", aliases=["icon"])
    @commands.guild_only()
    async def server_avatar(self, ctx: CustomContext):
        """ Get this server's icon. """
        if not ctx.guild.icon:
            return await ctx.send(embed=discord.Embed(description="❌ This server has no icon.", colour=discord.Colour.red()))
        fmts = ["JPEG", "PNG", "WebP"] + (["GIF"] if ctx.guild.icon.is_animated() else [])
        links = " **·** ".join(f"[{f}]({ctx.guild.icon.replace(format=f.lower(), size=1024)})" for f in fmts)
        embed = discord.Embed(title=f"🖼️ {ctx.guild.name} — Server Icon", description=links, colour=discord.Colour.blurple())
        embed.set_image(url=str(ctx.guild.icon.with_size(256).with_static_format("png")))
        await ctx.send(embed=embed)

    @server.command(name="banner")
    async def server_banner(self, ctx: CustomContext):
        """ Get this server's banner. """
        if not ctx.guild.banner:
            return await ctx.send(embed=discord.Embed(description="❌ This server has no banner.", colour=discord.Colour.red()))
        embed = discord.Embed(title=f"🎨 {ctx.guild.name} — Banner", colour=discord.Colour.blurple())
        embed.set_image(url=ctx.guild.banner.with_format("png"))
        await ctx.send(embed=embed)

    # ── User ──────────────────────────────────────────────────────────

    def _user_embed(self, user: discord.Member, guild: discord.Guild) -> discord.Embed:
        show_roles = ", ".join(
            f"<@&{x.id}>" for x in sorted(user.roles, key=lambda x: x.position, reverse=True)
            if x.id != guild.default_role.id
        ) or "None"
        msg_count = get_msg_count(guild.id, user.id)
        
        flags = ", ".join([f[0].replace("_", " ").title() for f in user.public_flags.all()]) or "None"
        status_emoji = {"online": "🟢", "idle": "🟡", "dnd": "🔴", "offline": "⚫"}
        status_name = str(user.status).title()
        status = f"{status_emoji.get(str(user.status), '⚫')} {status_name}"

        embed = discord.Embed(title=f"👤 {user}",
            colour=user.top_role.colour if user.top_role.colour.value else discord.Colour.blurple())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="🆔 User ID",          value=user.id)
        embed.add_field(name="🏷️ Nickname",         value=user.nick or "None")
        embed.add_field(name="📶 Status",          value=status)
        embed.add_field(name="📅 Account Created",  value=default.date(user.created_at, ago=True))
        embed.add_field(name="📥 Joined Server",    value=default.date(user.joined_at, ago=True))
        embed.add_field(name="💬 Messages Sent",    value=f"{msg_count:,}" if msg_count else "Not tracked yet")
        embed.add_field(name="🎖️ Badges",          value=flags)
        embed.add_field(name="🎭 Roles",            value=show_roles, inline=False)
        return embed

    @commands.command()
    @commands.guild_only()
    async def user(self, ctx: CustomContext, *, user: discord.Member = None):
        """ Get information about a user. """
        await ctx.send(embed=self._user_embed(user or ctx.author, ctx.guild))

    @app_commands.command(name="user", description="Get information about a user.")
    @app_commands.describe(user="The user to look up (default: yourself)")
    async def slash_user(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.send_message(embed=self._user_embed(user or interaction.user, interaction.guild))


    # ── Banner ────────────────────────────────────────────────────────

    @commands.hybrid_command(name="banner", description="Show a user's banner.")
    @commands.guild_only()
    @app_commands.describe(user="The user to check")
    async def banner(self, ctx: CustomContext, *, user: discord.Member = None):
        user = user or ctx.author
        fetched_user = await self.bot.fetch_user(user.id)
        if not fetched_user.banner:
            return await ctx.send(embed=discord.Embed(description=f"❌ **{fetched_user}** does not have a banner.", colour=discord.Colour.red()))
        embed = discord.Embed(title=f"🎨 {fetched_user}'s Banner", colour=user.top_role.colour if hasattr(user, 'top_role') and user.top_role.colour.value else discord.Colour.blurple())
        embed.set_image(url=fetched_user.banner.url)
        await ctx.send(embed=embed)

    # ── Member Count ──────────────────────────────────────────────────

    @commands.hybrid_command(name="membercount", aliases=["mc"], description="Show server member count details.")
    @commands.guild_only()
    async def membercount(self, ctx: CustomContext):
        g = ctx.guild
        humans = sum(1 for m in g.members if not m.bot)
        bots = sum(1 for m in g.members if m.bot)
        online = sum(1 for m in g.members if str(m.status) != "offline")
        embed = discord.Embed(title=f"👥 Member Count — {g.name}", colour=discord.Colour.blurple())
        embed.add_field(name="Total", value=len(g.members), inline=True)
        embed.add_field(name="Humans", value=humans, inline=True)
        embed.add_field(name="Bots", value=bots, inline=True)
        embed.add_field(name="Online", value=online, inline=True)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        await ctx.send(embed=embed)

    # ── Emoji ─────────────────────────────────────────────────────────

    @commands.hybrid_command(name="emoji", aliases=["enlarge", "jumbo"], description="Enlarge a custom emoji.")
    @commands.guild_only()
    @app_commands.describe(emoji_str="The custom emoji to enlarge")
    async def emoji(self, ctx: CustomContext, emoji_str: str):
        try:
            converter = commands.PartialEmojiConverter()
            emoji = await converter.convert(ctx, emoji_str)
        except commands.BadArgument:
            return await ctx.send(embed=discord.Embed(description="❌ Please provide a valid custom server emoji.", colour=discord.Colour.red()))
        
        embed = discord.Embed(title=f"Emoji: {emoji.name}", colour=discord.Colour.blurple())
        embed.set_image(url=emoji.url)
        embed.add_field(name="ID", value=f"`{emoji.id}`")
        embed.add_field(name="Animated", value="Yes" if emoji.animated else "No")
        embed.add_field(name="Use", value=f"`<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>`")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Discord_Info(bot))
