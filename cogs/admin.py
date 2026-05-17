import aiohttp
import discord
import importlib
import os

from discord.ext import commands
from discord import app_commands
from utils.default import CustomContext
from utils import permissions, default, http
from utils.data import DiscordBot


def owner_only_slash(interaction: discord.Interaction) -> bool:
    return interaction.user.id in interaction.client.config.discord_owner_ids


def _safe_name(name: str) -> str | None:
    """
    Validate a cog or utils module name.
    Only allows plain alphanumeric names and underscores — no dots, slashes,
    or path traversal sequences. Returns None if the name is unsafe.
    """
    import re
    if not name or not re.match(r'^[A-Za-z0-9_]+$', name):
        return None
    return name


def admin_or_owner_slash(interaction: discord.Interaction) -> bool:
    if interaction.user.id in interaction.client.config.discord_owner_ids:
        return True
    if isinstance(interaction.user, discord.Member):
        return interaction.user.guild_permissions.administrator
    return False


def staff_or_owner_slash(interaction: discord.Interaction) -> bool:
    """Allow staff (manage_messages or similar) and bot owners."""
    if interaction.user.id in interaction.client.config.discord_owner_ids:
        return True
    if isinstance(interaction.user, discord.Member):
        p = interaction.user.guild_permissions
        return (
            p.administrator
            or p.manage_guild
            or p.manage_messages
            or p.moderate_members
            or p.kick_members
            or p.ban_members
        )
    return False


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot

    # ── Load ──────────────────────────────────────────────────────────

    @commands.command()
    @commands.check(permissions.is_owner)
    async def load(self, ctx: CustomContext, name: str):
        """ Load a cog extension. """
        if not _safe_name(name):
            return await ctx.send("❌ Invalid cog name. Use plain letters, numbers and underscores only.")
        try:
            await self.bot.load_extension(f"cogs.{name}")
        except Exception as e:
            return await ctx.send(default.traceback_maker(e))
        await ctx.send(f"✅ Loaded **{name}.py**")

    # ── Unload ────────────────────────────────────────────────────────

    @commands.command()
    @commands.check(permissions.is_owner)
    async def unload(self, ctx: CustomContext, name: str):
        """ Unload a cog extension. """
        if not _safe_name(name):
            return await ctx.send("❌ Invalid cog name. Use plain letters, numbers and underscores only.")
        try:
            await self.bot.unload_extension(f"cogs.{name}")
        except Exception as e:
            return await ctx.send(default.traceback_maker(e))
        await ctx.send(f"✅ Unloaded **{name}.py**")

    # ── Reload ────────────────────────────────────────────────────────

    @commands.command()
    @commands.check(permissions.is_owner)
    async def reload(self, ctx: CustomContext, name: str):
        """ Reload a cog extension. """
        if not _safe_name(name):
            return await ctx.send("❌ Invalid cog name. Use plain letters, numbers and underscores only.")
        try:
            await self.bot.reload_extension(f"cogs.{name}")
        except Exception as e:
            return await ctx.send(default.traceback_maker(e))
        await ctx.send(f"✅ Reloaded **{name}.py**")

    # ── Reload All ────────────────────────────────────────────────────

    @commands.command()
    @commands.check(permissions.is_owner)
    async def reloadall(self, ctx: CustomContext):
        """ Reload all cog extensions. """
        errors = []
        for file in os.listdir("cogs"):
            if not file.endswith(".py"):
                continue
            try:
                await self.bot.reload_extension(f"cogs.{file[:-3]}")
            except Exception as e:
                errors.append([file, default.traceback_maker(e, advance=False)])
        if errors:
            output = "\n".join(f"**{g[0]}** ```diff\n- {g[1]}```" for g in errors)
            return await ctx.send(f"⚠️ Reloaded all, but these failed:\n\n{output}")
        await ctx.send("✅ Successfully reloaded all extensions.")

    # ── Reload Utils ──────────────────────────────────────────────────

    @commands.command()
    @commands.check(permissions.is_owner)
    async def reloadutils(self, ctx: CustomContext, name: str):
        """ Reload a utils module. """
        if not _safe_name(name):
            return await ctx.send("❌ Invalid module name. Use plain letters, numbers and underscores only.")
        try:
            module_name = importlib.import_module(f"utils.{name}")
            importlib.reload(module_name)
        except ModuleNotFoundError:
            return await ctx.send(f"❌ Couldn't find module **utils/{name}.py**")
        except Exception as e:
            return await ctx.send(f"⚠️ Module returned an error:\n{default.traceback_maker(e)}")
        await ctx.send(f"✅ Reloaded **utils/{name}.py**")

    # ── DM ────────────────────────────────────────────────────────────
    # Fixed: removed duplicate dm_error handler
    # Changed: staff (manage_messages+) can use it, not just admins
    # Added: embed with "sent by" footer

    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(manage_messages=True)
    async def dm(self, ctx: CustomContext, user_id: str, *, message: str):
        """
        DM a user. Usable by staff (manage_messages+) and bot owners.
        Usage: !dm @user message  OR  !dm 123456789 message
        """
        raw = user_id.strip("<@!>")
        try:
            uid = int(raw)
        except ValueError:
            return await ctx.send("❌ Could not parse that as a user. Use a @mention or a raw user ID.")

        try:
            user = await self.bot.fetch_user(uid)
        except discord.NotFound:
            return await ctx.send(f"❌ No user found with ID `{uid}`.")
        except discord.HTTPException as e:
            return await ctx.send(f"❌ Failed to fetch user: {e}")

        embed = discord.Embed(description=message, colour=discord.Colour.blurple())
        embed.set_footer(text=f"Sent by {ctx.author} ({ctx.author.id}) • {ctx.guild.name}")
        try:
            await user.send(embed=embed)
            await ctx.send(f"✉️ Sent a DM to **{user}** (`{user.id}`)")
        except discord.Forbidden:
            await ctx.send(f"❌ Could not DM **{user}** — they have DMs disabled or have blocked the bot.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to send DM: {e}")

    @dm.error
    async def dm_error(self, ctx: CustomContext, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Usage: `!dm <@user or user_id> <message>`")
        elif isinstance(error, commands.CheckFailure):
            pass  # has_permissions already sent the error embed
        else:
            await ctx.send(f"❌ Unexpected error: {error}")

    # ── Announce ──────────────────────────────────────────────────────

    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(administrator=True)
    async def announce(self, ctx: CustomContext, channel: discord.TextChannel, *, message: str):
        """
        Send an announcement embed to a channel. Admin or Owner only.
        Usage: !announce #channel Your message here
        """
        embed = discord.Embed(description=message, colour=discord.Colour.orange())
        embed.set_author(
            name="📢  Announcement",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )
        embed.set_footer(text=f"Sent by {ctx.author} ({ctx.author.id})")
        try:
            await channel.send(embed=embed)
            await ctx.send(f"✅ Announcement sent to {channel.mention}.")
        except discord.Forbidden:
            await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to send announcement: {e}")

    @announce.error
    async def announce_error(self, ctx: CustomContext, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Usage: `!announce #channel <message>`")
        elif isinstance(error, commands.ChannelNotFound):
            await ctx.send("❌ Channel not found. Mention it or use its ID.")
        elif isinstance(error, commands.CheckFailure):
            pass
        else:
            await ctx.send(f"❌ Unexpected error: {error}")

    # ── Change Username ───────────────────────────────────────────────

    @commands.group()
    @commands.check(permissions.is_owner)
    async def change(self, ctx: CustomContext):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(str(ctx.command))

    @change.command(name="username")
    @commands.check(permissions.is_owner)
    async def change_username(self, ctx: CustomContext, *, name: str):
        """ Change the bot's username. """
        try:
            await self.bot.user.edit(username=name)
            await ctx.send(f"✅ Username changed to **{name}**")
        except discord.HTTPException as err:
            await ctx.send(err)

    # ── Change Avatar ─────────────────────────────────────────────────

    @change.command(name="avatar")
    @commands.check(permissions.is_owner)
    async def change_avatar(self, ctx: CustomContext, url: str = None):
        """ Change the bot's avatar. """
        if url is None and len(ctx.message.attachments) == 1:
            url = ctx.message.attachments[0].url
        elif url:
            url = url.strip("<>")
        try:
            bio = await http.get(url, res_method="read")
            await self.bot.user.edit(avatar=bio.response)
            await ctx.send("✅ Avatar updated successfully.")
        except aiohttp.InvalidURL:
            await ctx.send("❌ The URL provided is invalid.")
        except discord.InvalidArgument:
            await ctx.send("❌ That URL doesn't contain a valid image.")
        except discord.HTTPException as err:
            await ctx.send(err)
        except TypeError:
            await ctx.send("❌ Please provide an image URL or attach an image.")


async def setup(bot):
    await bot.add_cog(Admin(bot))
