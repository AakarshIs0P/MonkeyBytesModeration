import asyncio
import json
import os
from typing import Optional

import discord
from discord.ext import commands

from utils.default import CustomContext
from utils.data import ACCENT_COLOUR, DiscordBot


DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data",
    "sticky_messages.json",
)

REFRESH_DELAY_SECONDS = 2.0


def _load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_data(data: dict) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)


class Sticky(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot
        self.data = _load_data()
        self._save_lock = asyncio.Lock()
        self._refresh_tasks: dict[int, asyncio.Task] = {}

    def cog_unload(self):
        for task in self._refresh_tasks.values():
            task.cancel()

    def _guild_data(self, guild_id: int) -> dict:
        return self.data.setdefault(str(guild_id), {})

    def _get_config(self, guild_id: int, channel_id: int) -> dict | None:
        return self._guild_data(guild_id).get(str(channel_id))

    async def _save(self):
        async with self._save_lock:
            _save_data(self.data)

    async def _delete_old_sticky(self, channel: discord.TextChannel, message_id: int | None):
        if not message_id:
            return
        try:
            old_message = await channel.fetch_message(message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _post_sticky(self, channel: discord.TextChannel, config: dict):
        await self._delete_old_sticky(channel, config.get("message_id"))
        message = await channel.send(config["content"])
        config["message_id"] = message.id
        await self._save()

    async def _refresh_later(self, channel: discord.TextChannel):
        try:
            await asyncio.sleep(REFRESH_DELAY_SECONDS)
            if not channel.guild:
                return

            config = self._get_config(channel.guild.id, channel.id)
            if not config:
                return

            me = channel.guild.me
            if not me:
                return

            perms = channel.permissions_for(me)
            if not perms.send_messages:
                return

            await self._post_sticky(channel, config)
        finally:
            self._refresh_tasks.pop(channel.id, None)

    def _schedule_refresh(self, channel: discord.TextChannel):
        existing = self._refresh_tasks.get(channel.id)
        if existing and not existing.done():
            existing.cancel()
        self._refresh_tasks[channel.id] = asyncio.create_task(self._refresh_later(channel))

    @commands.group(name="sticky", invoke_without_command=True)
    @commands.guild_only()
    async def sticky(self, ctx: CustomContext):
        """Manage sticky messages for channels."""
        prefix = ctx.clean_prefix or ctx.prefix
        embed = discord.Embed(
            title="Sticky Messages",
            description="Keep one message refreshed at the bottom of a channel.",
            colour=ACCENT_COLOUR,
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{prefix}sticky here <message>`\n"
                f"`{prefix}sticky set #channel <message>`\n"
                f"`{prefix}sticky clear [#channel]`\n"
                f"`{prefix}sticky show [#channel]`\n"
                f"`{prefix}sticky list`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @sticky.command(name="here")
    @commands.has_permissions(manage_messages=True)
    async def sticky_here(self, ctx: CustomContext, *, content: str):
        """Set a sticky message in this channel."""
        await self._set_sticky(ctx, ctx.channel, content)

    @sticky.command(name="set")
    @commands.has_permissions(manage_messages=True)
    async def sticky_set(self, ctx: CustomContext, channel: discord.TextChannel, *, content: str):
        """Set a sticky message in a specific channel."""
        await self._set_sticky(ctx, channel, content)

    async def _set_sticky(self, ctx: CustomContext, channel: discord.TextChannel, content: str):
        if not content.strip():
            return await ctx.send("Sticky message cannot be empty.")

        me = channel.guild.me
        if not me:
            return await ctx.send("I could not check my permissions in that channel.")

        perms = channel.permissions_for(me)
        missing = []
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.read_message_history:
            missing.append("Read Message History")
        if not perms.manage_messages:
            missing.append("Manage Messages")
        if missing:
            return await ctx.send(
                f"I need these permissions in {channel.mention}: {', '.join(missing)}."
            )

        guild_data = self._guild_data(ctx.guild.id)
        old_config = guild_data.get(str(channel.id), {})
        config = {
            "content": content.strip(),
            "message_id": old_config.get("message_id"),
            "author_id": ctx.author.id,
        }
        guild_data[str(channel.id)] = config

        await self._post_sticky(channel, config)
        await ctx.send(f"Sticky message set for {channel.mention}.")

    @sticky.command(name="clear", aliases=["remove", "delete"])
    @commands.has_permissions(manage_messages=True)
    async def sticky_clear(self, ctx: CustomContext, channel: Optional[discord.TextChannel] = None):
        """Clear a sticky message from a channel."""
        channel = channel or ctx.channel
        guild_data = self._guild_data(ctx.guild.id)
        config = guild_data.pop(str(channel.id), None)
        if not config:
            return await ctx.send(f"No sticky message is set for {channel.mention}.")

        await self._delete_old_sticky(channel, config.get("message_id"))
        await self._save()
        await ctx.send(f"Sticky message cleared for {channel.mention}.")

    @sticky.command(name="show")
    @commands.has_permissions(manage_messages=True)
    async def sticky_show(self, ctx: CustomContext, channel: Optional[discord.TextChannel] = None):
        """Show the configured sticky message for a channel."""
        channel = channel or ctx.channel
        config = self._get_config(ctx.guild.id, channel.id)
        if not config:
            return await ctx.send(f"No sticky message is set for {channel.mention}.")

        embed = discord.Embed(
            title=f"Sticky for #{channel.name}",
            description=config["content"],
            colour=ACCENT_COLOUR,
        )
        await ctx.send(embed=embed)

    @sticky.command(name="list")
    @commands.has_permissions(manage_messages=True)
    async def sticky_list(self, ctx: CustomContext):
        """List all sticky messages in this server."""
        guild_data = self._guild_data(ctx.guild.id)
        if not guild_data:
            return await ctx.send("No sticky messages are set in this server.")

        lines = []
        for channel_id, config in guild_data.items():
            channel = ctx.guild.get_channel(int(channel_id))
            if channel:
                preview = config["content"].replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                lines.append(f"{channel.mention}: {preview}")

        if not lines:
            return await ctx.send("Sticky messages are configured, but their channels no longer exist.")

        embed = discord.Embed(
            title="Sticky Messages",
            description="\n".join(lines),
            colour=ACCENT_COLOUR,
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        config = self._get_config(message.guild.id, message.channel.id)
        if not config:
            return

        if message.id == config.get("message_id"):
            return

        self._schedule_refresh(message.channel)

    def help_embed(self, prefix: str = "!", guild=None) -> discord.Embed:
        embed = discord.Embed(
            title="Sticky Messages",
            description="Keep an important message refreshed near the bottom of a channel.",
            colour=ACCENT_COLOUR,
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{prefix}sticky here <message>`\n"
                f"`{prefix}sticky set #channel <message>`\n"
                f"`{prefix}sticky clear [#channel]`\n"
                f"`{prefix}sticky show [#channel]`\n"
                f"`{prefix}sticky list`"
            ),
            inline=False,
        )
        embed.set_footer(text="Requires Manage Messages.")
        return embed


async def setup(bot):
    await bot.add_cog(Sticky(bot))
