import discord
import logging
from discord.ext import commands
from utils.default import CustomContext
from utils.json_store import AtomicJSONStore
from utils import permissions
from cogs.warns import _add_warn
from utils.data import DiscordBot

log = logging.getLogger("bot.cogs.antiping")

class AntiPing(commands.Cog, name="AntiPing"):
    """Prevent users from being pinged and issue warnings automatically."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self.store = AtomicJSONStore("data/antiping.json", default_factory=dict)
        self.bot.loop.create_task(self.store.load())

    def _is_admin(self, member: discord.Member) -> bool:
        """Check if a member has admin or manage_messages permission, or is owner."""
        if member.guild.owner_id == member.id:
            return True
        if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
            return True
        # Check against bot owners from permissions
        if member.id in permissions.OWNERS:
            return True
        return False

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def antiping(self, ctx: CustomContext):
        """Manage the Anti-Ping system for yourself or others."""
        await ctx.send_help(ctx.command)

    @antiping.command(name="toggle")
    @commands.guild_only()
    async def antiping_toggle(self, ctx: CustomContext):
        """Toggle your own anti-ping status."""
        guild_id = str(ctx.guild.id)
        user_id = str(ctx.author.id)

        data = await self.store.load()
        if guild_id not in data:
            data[guild_id] = []
        
        if user_id in data[guild_id]:
            data[guild_id].remove(user_id)
            status = "Disabled"
        else:
            data[guild_id].append(user_id)
            status = "Enabled"
            
        self.store.mark_dirty()
        await self.store.flush()
        
        embed = discord.Embed(
            title="🔕 Anti-Ping Toggled",
            description=f"**{status}** anti-ping for {ctx.author.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @antiping.command(name="add")
    @commands.guild_only()
    @permissions.has_permissions(manage_messages=True)
    async def antiping_add(self, ctx: CustomContext, member: discord.Member):
        """(Admin) Add a member to the anti-ping list."""
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        
        data = await self.store.load()
        if guild_id not in data:
            data[guild_id] = []
            
        if user_id in data[guild_id]:
            await ctx.send(embed=discord.Embed(
                description=f"❌ {member.mention} is already on the anti-ping list.",
                color=discord.Color.red()
            ))
            return
            
        data[guild_id].append(user_id)
        self.store.mark_dirty()
        await self.store.flush()
        
        await ctx.send(embed=discord.Embed(
            title="🔕 Anti-Ping Added",
            description=f"Added {member.mention} to the anti-ping list.",
            color=discord.Color.green()
        ))

    @antiping.command(name="remove")
    @commands.guild_only()
    @permissions.has_permissions(manage_messages=True)
    async def antiping_remove(self, ctx: CustomContext, member: discord.Member):
        """(Admin) Remove a member from the anti-ping list."""
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        
        data = await self.store.load()
        if guild_id not in data or user_id not in data[guild_id]:
            await ctx.send(embed=discord.Embed(
                description=f"❌ {member.mention} is not on the anti-ping list.",
                color=discord.Color.red()
            ))
            return
            
        data[guild_id].remove(user_id)
        self.store.mark_dirty()
        await self.store.flush()
        
        await ctx.send(embed=discord.Embed(
            title="🔕 Anti-Ping Removed",
            description=f"Removed {member.mention} from the anti-ping list.",
            color=discord.Color.green()
        ))
        
    @antiping.command(name="list")
    @commands.guild_only()
    async def antiping_list(self, ctx: CustomContext):
        """List all members with anti-ping enabled."""
        guild_id = str(ctx.guild.id)
        data = await self.store.load()
        
        users = data.get(guild_id, [])
        if not users:
            await ctx.send(embed=discord.Embed(
                description="🔕 Nobody is on the anti-ping list in this server.",
                color=discord.Color.blue()
            ))
            return
            
        mentions = []
        for uid in users:
            mentions.append(f"<@{uid}>")
            
        embed = discord.Embed(
            title="🔕 Anti-Ping List",
            description="\n".join(mentions) if len(mentions) <= 50 else "\n".join(mentions[:50]) + "\n...and more.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages, DMs, or empty mentions
        if message.author.bot or not message.guild or not message.mentions:
            return

        # If the author has admin/mod permissions, they can ping anyone
        if self._is_admin(message.author):
            return

        guild_id = str(message.guild.id)
        data = await self.store.load()
        protected_users = data.get(guild_id, [])
        
        if not protected_users:
            return

        # Check if any mentioned user is in the protected list (and isn't the author themselves)
        pinged_protected = None
        for member in message.mentions:
            if str(member.id) in protected_users and member.id != message.author.id:
                pinged_protected = member
                break
                
        if pinged_protected:
            try:
                # Try to delete the message
                await message.delete()
            except discord.Forbidden:
                pass
            except discord.NotFound:
                pass
                
            # Issue a warning
            reason = f"Pinged a protected user ({pinged_protected.name})"
            try:
                count = await _add_warn(str(message.guild.id), str(message.author.id), reason, str(self.bot.user), self.bot.user.id)
            except Exception as e:
                log.error(f"Failed to add warning to {message.author}: {e}")
                count = "?"
                
            # Notify the user
            try:
                warn_msg = await message.channel.send(
                    embed=discord.Embed(
                        title="⚠️ Warning Issued",
                        description=f"{message.author.mention}, you cannot ping {pinged_protected.mention}. Your message was deleted.",
                        color=discord.Color.red()
                    )
                )
                # Delete the warning message after a few seconds to avoid clutter
                await warn_msg.delete(delay=10)
            except (discord.Forbidden, discord.NotFound):
                pass
                
async def setup(bot: DiscordBot):
    await bot.add_cog(AntiPing(bot))
