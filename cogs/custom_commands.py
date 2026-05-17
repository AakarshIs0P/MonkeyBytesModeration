import discord
import json
import os
import asyncio

from discord.ext import commands, tasks
from utils.default import CustomContext
from utils.data import DiscordBot

CC_FILE = "data/custom_commands.json"
_cc_cache = {}
_cc_dirty = False
_cc_lock = asyncio.Lock()

def _ensure_data_dir():
    if not os.path.exists("data"):
        os.makedirs("data")

class CustomCommands(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot
        _ensure_data_dir()
        try:
            with open(CC_FILE, "r") as f:
                global _cc_cache
                _cc_cache = json.load(f)
        except Exception:
            _cc_cache = {}
        self.save_cc_loop.start()

    def cog_unload(self):
        self.save_cc_loop.cancel()
        if _cc_dirty:
            with open(CC_FILE, "w") as f:
                json.dump(_cc_cache, f, indent=2)

    @tasks.loop(seconds=60)
    async def save_cc_loop(self):
        global _cc_dirty
        async with _cc_lock:
            if _cc_dirty:
                tmp = CC_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(_cc_cache, f, indent=2)
                os.replace(tmp, CC_FILE)
                _cc_dirty = False

    @commands.command(name="create")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def create_cc(self, ctx: CustomContext, trigger: str, *, response: str):
        """
        Creates a custom command.
        
        Usage: !create <trigger> <response> [--perm <permission>]
        Example: !create haha Hahaha
        Example: !create modhelp Here is the mod help! --perm manage_messages
        """
        global _cc_dirty
        guild_id = str(ctx.guild.id)
        trigger = trigger.lower()
        
        # Prevent overriding existing bot commands
        if self.bot.get_command(trigger):
            return await ctx.send(f"❌ You cannot override the built-in `{trigger}` command.")
            
        required_perm = None
        if "--perm " in response:
            parts = response.split("--perm ")
            response = parts[0].strip()
            required_perm = parts[1].strip().lower()
            
            valid_perms = [p for p, _ in discord.Permissions()]
            if required_perm not in valid_perms and required_perm not in ["everyone", "none"]:
                return await ctx.send(f"❌ Invalid permission `{required_perm}`.\nValid examples: `manage_messages`, `administrator`, `ban_members`.")
                
        if required_perm in ["everyone", "none"]:
            required_perm = None
            
        async with _cc_lock:
            if guild_id not in _cc_cache:
                _cc_cache[guild_id] = {}
            
            _cc_cache[guild_id][trigger] = {
                "response": response,
                "permission": required_perm,
                "author": ctx.author.id
            }
            _cc_dirty = True
            
        perm_text = f" (Requires: `{required_perm}`)" if required_perm else " (Available to everyone)"
        await ctx.send(f"✅ Custom command `{ctx.prefix}{trigger}` created!{perm_text}")

    @commands.command(name="deletecc", aliases=["delcc", "removecc"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def delete_cc(self, ctx: CustomContext, trigger: str):
        """Deletes a custom command."""
        global _cc_dirty
        guild_id = str(ctx.guild.id)
        trigger = trigger.lower()
        
        async with _cc_lock:
            if guild_id in _cc_cache and trigger in _cc_cache[guild_id]:
                del _cc_cache[guild_id][trigger]
                _cc_dirty = True
                await ctx.send(f"🗑️ Custom command `{ctx.prefix}{trigger}` has been deleted.")
            else:
                await ctx.send(f"❌ Custom command `{ctx.prefix}{trigger}` not found.")

    @commands.command(name="listcc", aliases=["customcommands", "ccs"])
    @commands.guild_only()
    async def list_cc(self, ctx: CustomContext):
        """Lists all custom commands for this server."""
        guild_id = str(ctx.guild.id)
        
        if guild_id not in _cc_cache or not _cc_cache[guild_id]:
            return await ctx.send("📋 There are no custom commands in this server.")
            
        embed = discord.Embed(title="📋 Custom Commands", color=discord.Color.blue())
        
        for trigger, data in _cc_cache[guild_id].items():
            perm = data.get("permission")
            perm_text = f"**Requires:** `{perm}`" if perm else "Available to everyone"
            embed.add_field(name=f"{ctx.prefix}{trigger}", value=perm_text, inline=True)
            
        await ctx.send(embed=embed)

    def help_embed(self, prefix: str = "!", guild=None) -> discord.Embed:
        embed = discord.Embed(
            title="Custom Commands",
            description="Server admins can create simple text commands for this server.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Manage Commands",
            value=(
                f"`{prefix}create <trigger> <response>`\n"
                f"`{prefix}create <trigger> <response> --perm <permission>`\n"
                f"`{prefix}deletecc <trigger>`\n"
                f"`{prefix}listcc`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Examples",
            value=(
                f"`{prefix}create rules Please read #rules first.`\n"
                f"`{prefix}create modhelp Staff notes here --perm manage_messages`\n"
                f"`{prefix}rules` runs the custom command after it is created."
            ),
            inline=False,
        )

        if guild:
            commands_for_guild = _cc_cache.get(str(guild.id), {})
            if commands_for_guild:
                names = sorted(commands_for_guild)[:20]
                shown = ", ".join(f"`{prefix}{name}`" for name in names)
                more = len(commands_for_guild) - len(names)
                if more > 0:
                    shown += f"\n...and {more} more. Use `{prefix}listcc` to see all."
                embed.add_field(name="This Server's Custom Commands", value=shown, inline=False)
            else:
                embed.add_field(
                    name="This Server's Custom Commands",
                    value=f"None yet. Create one with `{prefix}create <trigger> <response>`.",
                    inline=False,
                )

        embed.set_footer(text="Only administrators can create or delete custom commands.")
        return embed

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            # It's a real command, let the bot handle it
            return
            
        prefix = ctx.prefix
        if not prefix or not message.content.startswith(prefix):
            return
            
        # Extract the command trigger
        trigger = message.content[len(prefix):].split(" ")[0].lower()
        
        guild_id = str(message.guild.id)
        if guild_id in _cc_cache and trigger in _cc_cache[guild_id]:
            cc = _cc_cache[guild_id][trigger]
            
            # Check permissions
            req_perm = cc.get("permission")
            if req_perm:
                perms = message.channel.permissions_for(message.author)
                if not getattr(perms, req_perm, False) and not perms.administrator:
                    return
                    
            await message.channel.send(cc["response"])

async def setup(bot):
    await bot.add_cog(CustomCommands(bot))
