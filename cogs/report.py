import discord
import json
import os
import asyncio
import datetime
import uuid

from discord.ext import commands
from utils.default import CustomContext
from utils.data import DiscordBot
from utils.permissions import OWNERS

# ── Colour palette (mirrors mod.py) ───────────────────────────────────────────

COL_SUCCESS = discord.Colour.green()
COL_ERROR   = discord.Colour.red()
COL_WARN    = discord.Colour.orange()
COL_INFO    = discord.Colour.blurple()
COL_REPORT  = discord.Colour.from_str("#E74C3C")
COL_CONFIRM = discord.Colour.from_str("#F39C12")
COL_PURPLE  = discord.Colour.from_str("#9B59B6")
COL_DARK    = discord.Colour.from_str("#2C3E50")

# ── Embed helpers (mirrors mod.py) ────────────────────────────────────────────

def err(text):
    return discord.Embed(description=f"❌  {text}", colour=COL_ERROR)

def ok(text):
    return discord.Embed(description=f"✅  {text}", colour=COL_SUCCESS)

def info_emb(text):
    return discord.Embed(description=f"ℹ️  {text}", colour=COL_INFO)

def warn_emb(text):
    return discord.Embed(description=f"⚠️  {text}", colour=COL_WARN)

# ── Constants ─────────────────────────────────────────────────────────────────

REPORT_DATA_PATH = "data/report_channels.json"
EXIT_KEYWORD     = "!exit"
DONE_KEYWORD     = "!done"
FLOW_TIMEOUT     = 180.0   # seconds per step before auto-cancel

# ── Exceptions ────────────────────────────────────────────────────────────────

class ExitReport(Exception):
    """Raised when the user types !exit during any step of the report flow."""

class TimeoutReport(Exception):
    """Raised when the user does not respond within FLOW_TIMEOUT seconds."""

# ── Persistence helpers ───────────────────────────────────────────────────────

def _load_data() -> dict:
    """Load the report-channel config from disk."""
    if not os.path.exists(REPORT_DATA_PATH):
        return {}
    try:
        with open(REPORT_DATA_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_data(data: dict) -> None:
    """Persist the report-channel config to disk."""
    os.makedirs(os.path.dirname(REPORT_DATA_PATH), exist_ok=True)
    with open(REPORT_DATA_PATH, "w") as f:
        json.dump(data, f, indent=2)

# ── Admin permission check (same pattern as mod.py) ──────────────────────────

def admin_check():
    """
    Decorator: allows OWNERS, server owner, and members with Manage Server.
    Sends an error embed on failure.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in OWNERS:
            return True
        if ctx.guild and ctx.author.id == ctx.guild.owner_id:
            return True
        if ctx.guild and ctx.author.guild_permissions.manage_guild:
            return True

        embed = discord.Embed(
            description="❌  You need the `Manage Server` permission to use this.",
            colour=COL_ERROR,
        )
        try:
            await ctx.send(embed=embed, ephemeral=True)
        except Exception:
            pass
        return False

    return commands.check(predicate)

# ── Cog ───────────────────────────────────────────────────────────────────────

class Reporter(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot
        # Tracks user IDs that are currently mid-report to block duplicates.
        self._active: set[int] = set()

    # ── Error handler ────────────────────────────────────────────────────────

    async def cog_command_error(self, ctx: CustomContext, error: commands.CommandError):
        if isinstance(error, commands.PrivateMessageOnly):
            try:
                await ctx.message.add_reaction("📩")
            except Exception:
                pass
            try:
                await ctx.send(
                    embed=err("The `!report` command can only be used in my **DMs**. Slide into my DMs to submit a report!"),
                    ephemeral=True,
                )
            except Exception:
                pass
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send(embed=err("This command cannot be used in DMs."), ephemeral=True)
        elif isinstance(error, commands.CheckFailure):
            pass   # check already sent its own embed
        else:
            raise error

    # ─────────────────────────────────────────────────────────────────────────
    # !setreport  (guild-only, admin)
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="setreport", aliases=["reportchannel", "set-report"])
    @commands.guild_only()
    @admin_check()
    async def setreport(self, ctx: CustomContext, channel: discord.TextChannel = None):
        """
        Set the channel where user reports are delivered.

        Usage:
          !setreport             → uses the current channel
          !setreport #channel    → uses the mentioned channel
        """
        target = channel or ctx.channel

        # Verify the bot can send messages and create threads there
        bot_member = ctx.guild.get_member(self.bot.user.id)
        perms = target.permissions_for(bot_member)
        if not perms.send_messages or not perms.embed_links or not perms.create_public_threads:
            return await ctx.send(
                embed=err(
                    f"I don't have **Send Messages**, **Embed Links**, and **Create Public Threads** permissions in {target.mention}. "
                    "Please fix my permissions there first."
                ),
                ephemeral=True,
            )

        data = _load_data()
        old_id = data.get(str(ctx.guild.id))
        data[str(ctx.guild.id)] = target.id
        _save_data(data)

        embed = discord.Embed(
            title="📋  Report Channel Configured",
            colour=COL_SUCCESS,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="📺  Channel", value=target.mention, inline=True)
        embed.add_field(name="🔧  Set by",  value=ctx.author.mention, inline=True)
        if old_id and old_id != target.id:
            old_ch = ctx.guild.get_channel(old_id)
            old_str = old_ch.mention if old_ch else f"`{old_id}`"
            embed.add_field(name="♻️  Previous", value=old_str, inline=True)
        embed.set_footer(text=f"{ctx.guild.name}")
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # !reportinfo  (guild-only, admin convenience)
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="reportinfo", aliases=["checkreport"])
    @commands.guild_only()
    @admin_check()
    async def reportinfo(self, ctx: CustomContext):
        """Show the currently configured report channel for this server."""
        data = _load_data()
        channel_id = data.get(str(ctx.guild.id))

        if not channel_id:
            return await ctx.send(
                embed=warn_emb(
                    "No report channel has been set for this server. "
                    "Use `!setreport` or `!setreport #channel` to configure one."
                )
            )

        channel = ctx.guild.get_channel(channel_id)
        if channel:
            embed = discord.Embed(
                title="📋  Report Channel",
                description=f"Reports for **{ctx.guild.name}** are delivered to {channel.mention}.",
                colour=COL_INFO,
            )
            embed.set_footer(text=f"Channel ID: {channel_id}")
        else:
            embed = discord.Embed(
                title="📋  Report Channel — Not Found",
                description=(
                    f"A report channel was configured (`{channel_id}`) but it no longer exists. "
                    "Please run `!setreport` again."
                ),
                colour=COL_WARN,
            )
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # !clearreport  (guild-only, admin convenience)
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="clearreport", aliases=["removereport"])
    @commands.guild_only()
    @admin_check()
    async def clearreport(self, ctx: CustomContext):
        """Remove the report channel configuration for this server."""
        data = _load_data()
        if str(ctx.guild.id) not in data:
            return await ctx.send(
                embed=warn_emb("No report channel is currently set for this server.")
            )
        del data[str(ctx.guild.id)]
        _save_data(data)
        await ctx.send(embed=ok("Report channel configuration cleared. Members will no longer be able to submit reports for this server."))

    # ─────────────────────────────────────────────────────────────────────────
    # !report  (DM-only)
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="report")
    @commands.dm_only()
    @commands.cooldown(2, 300, commands.BucketType.user)  # max 2 reports per 5 min per user
    async def report(self, ctx: CustomContext):
        """
        Submit a report against a user. Must be used in bot DMs.
        Walks you through the full report flow step-by-step.
        """
        author = ctx.author

        if author.id in self._active:
            return await ctx.send(
                embed=err(
                    "You already have a report in progress. "
                    "Type `!exit` in this DM to cancel it, then try again."
                )
            )

        self._active.add(author.id)
        try:
            await self._run_report_flow(ctx, author)
        finally:
            self._active.discard(author.id)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: wait-for helper
    # ─────────────────────────────────────────────────────────────────────────

    async def _wait_for_message(
        self,
        author: discord.User,
        timeout: float = FLOW_TIMEOUT,
    ) -> discord.Message:
        """
        Block until the given user sends a DM.
        Raises ExitReport  if they type !exit.
        Raises TimeoutReport if they don't respond in time.
        """
        def check(m: discord.Message) -> bool:
            return (
                m.author.id == author.id
                and isinstance(m.channel, discord.DMChannel)
            )

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutReport()

        if msg.content.strip().lower() == EXIT_KEYWORD:
            raise ExitReport()

        return msg

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: full report flow
    # ─────────────────────────────────────────────────────────────────────────

    async def _run_report_flow(self, ctx: CustomContext, author: discord.User):
        dm = ctx.channel

        try:

            # ── Welcome ──────────────────────────────────────────────────────

            welcome = discord.Embed(
                title="📋  Report a User",
                description=(
                    "Welcome to the report system. I'll guide you through every step.\n\n"
                    "**⛔ Type `!exit` at any time to cancel the report.**\n\n"
                    "Please be honest and provide as much evidence as possible. "
                    "Reports are reviewed by server staff. "
                    "Submitting false or malicious reports may result in consequences."
                ),
                colour=COL_REPORT,
                timestamp=datetime.datetime.utcnow(),
            )
            welcome.set_footer(text="Type !exit at any time to cancel · Steps: 1 of 4")
            await dm.send(embed=welcome)

            # ── Step 1: Choose server ─────────────────────────────────────────

            data = _load_data()

            # Find guilds the user shares with the bot that have a report channel set
            mutual_guilds = [g for g in self.bot.guilds if g.get_member(author.id)]
            valid_guilds  = [g for g in mutual_guilds if str(g.id) in data]

            if not valid_guilds:
                if mutual_guilds:
                    return await dm.send(
                        embed=err(
                            "None of the servers we share have set up a report channel yet. "
                            "Ask a server admin to run `!setreport` first."
                        )
                    )
                else:
                    return await dm.send(
                        embed=err(
                            "I couldn't find any shared servers with you. "
                            "Make sure you are a member of a server that uses this bot."
                        )
                    )

            target_guild: discord.Guild

            if len(valid_guilds) == 1:
                target_guild = valid_guilds[0]
                await dm.send(
                    embed=info_emb(f"Submitting report to: **{target_guild.name}**")
                )
            else:
                guild_list = "\n".join(
                    f"`{i + 1}.`  {g.name}" for i, g in enumerate(valid_guilds)
                )
                step1_embed = discord.Embed(
                    title="📋  Step 1 of 4 — Choose Server",
                    description=(
                        f"Which server is this report for?\n\n{guild_list}\n\n"
                        "Reply with the **number** next to the server."
                    ),
                    colour=COL_INFO,
                )
                step1_embed.set_footer(text="Type !exit to cancel")
                await dm.send(embed=step1_embed)

                while True:
                    resp = await self._wait_for_message(author)
                    try:
                        idx = int(resp.content.strip()) - 1
                        if 0 <= idx < len(valid_guilds):
                            target_guild = valid_guilds[idx]
                            await dm.send(
                                embed=ok(f"Got it — filing report for **{target_guild.name}**.")
                            )
                            break
                        else:
                            raise ValueError
                    except ValueError:
                        await dm.send(
                            embed=err(f"Please reply with a number between 1 and {len(valid_guilds)}.")
                        )

            # ── Step 2: Reported user ID ─────────────────────────────────────

            step2_embed = discord.Embed(
                title="📋  Step 2 of 4 — User to Report",
                description=(
                    "Please send the **User ID** of the person you are reporting.\n\n"
                    "**How to find a User ID:**\n"
                    "• Open Discord **Settings → Advanced** and enable **Developer Mode**\n"
                    "• Right-click (or long-press) the user → **Copy User ID**\n\n"
                    "You can also `@mention` them if they are in the server."
                ),
                colour=COL_INFO,
            )
            step2_embed.set_footer(text="Type !exit to cancel")
            await dm.send(embed=step2_embed)

            reported_user: discord.User = await self._ask_for_user(dm, author)

            # ── Step 2b: Confirm the identity ────────────────────────────────

            reported_user = await self._confirm_user(dm, author, reported_user)

            # ── Step 3: Reason ───────────────────────────────────────────────

            step3_embed = discord.Embed(
                title="📋  Step 3 of 4 — Reason",
                description=(
                    "In a few sentences, describe **why** you are reporting this user.\n\n"
                    "• What did they do?\n"
                    "• Where did it happen (channel name, DMs, etc.)?\n"
                    "• When did it happen (approximate date/time)?"
                ),
                colour=COL_INFO,
            )
            step3_embed.set_footer(text="Type !exit to cancel")
            await dm.send(embed=step3_embed)

            reason_msg   = await self._wait_for_message(author, timeout=300.0)
            report_reason = reason_msg.content.strip() or "No reason provided."
            await dm.send(embed=ok("Reason recorded."))

            # ── Step 4: Evidence ─────────────────────────────────────────────

            step4_embed = discord.Embed(
                title="📋  Step 4 of 4 — Evidence",
                description=(
                    "Now send your **evidence**. You can send multiple messages — each one is saved.\n\n"
                    "Accepted evidence types:\n"
                    "📸  Screenshots / images\n"
                    "📄  Text descriptions or copy-pasted messages\n"
                    "🔗  Discord message links\n"
                    "📎  Any other relevant files\n\n"
                    "Type **`!done`** when you have finished sending evidence.\n"
                    "*(Type `!exit` to cancel the entire report)*"
                ),
                colour=COL_INFO,
            )
            step4_embed.set_footer(
                text="Send evidence now • Type !done when finished • !exit to cancel"
            )
            await dm.send(embed=step4_embed)

            evidence_msgs: list[discord.Message] = []

            while True:
                resp = await self._wait_for_message(author, timeout=300.0)

                if resp.content.strip().lower() == DONE_KEYWORD:
                    if not evidence_msgs:
                        await dm.send(
                            embed=warn_emb(
                                "You haven't sent any evidence yet. "
                                "Please send at least one message, image, or file as evidence, "
                                "or type `!exit` to cancel the report."
                            )
                        )
                        continue
                    break

                evidence_msgs.append(resp)
                count = len(evidence_msgs)
                ack = discord.Embed(
                    description=(
                        f"✅  Evidence piece **{count}** received. "
                        f"Send more or type `!done` to finish."
                    ),
                    colour=COL_SUCCESS,
                )
                await dm.send(embed=ack)

            # ── Build & send the report ───────────────────────────────────────

            report_id  = "RPT-" + str(uuid.uuid4())[:6].upper()
            now        = datetime.datetime.utcnow()

            # Resolve report channel
            channel_id     = data[str(target_guild.id)]
            report_channel = target_guild.get_channel(channel_id)
            if report_channel is None:
                try:
                    report_channel = await self.bot.fetch_channel(channel_id)
                except Exception:
                    return await dm.send(
                        embed=err(
                            "The configured report channel no longer exists in "
                            f"**{target_guild.name}**. Please inform a server admin."
                        )
                    )

            # ── Main report embed ─────────────────────────────────────────────

            member_in_guild = target_guild.get_member(reported_user.id)

            report_embed = discord.Embed(
                title=f"🚨  New User Report  ·  {report_id}",
                colour=COL_REPORT,
                timestamp=now,
            )
            report_embed.set_thumbnail(url=reported_user.display_avatar.url)
            report_embed.set_author(
                name=f"Report filed by {author}",
                icon_url=author.display_avatar.url,
            )

            # Reporter
            report_embed.add_field(
                name="📢  Reporter",
                value=f"{author.mention}\n`{author}`\n`ID: {author.id}`",
                inline=True,
            )

            # Reported user
            report_embed.add_field(
                name="🎯  Reported User",
                value=f"{reported_user.mention}\n`{reported_user}`\n`ID: {reported_user.id}`",
                inline=True,
            )

            report_embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer

            # Account creation
            report_embed.add_field(
                name="📆  Account Created",
                value=discord.utils.format_dt(reported_user.created_at, "R"),
                inline=True,
            )

            # Server membership info
            if member_in_guild:
                joined_str = (
                    discord.utils.format_dt(member_in_guild.joined_at, "R")
                    if member_in_guild.joined_at
                    else "Unknown"
                )
                report_embed.add_field(
                    name="📅  Joined Server",
                    value=joined_str,
                    inline=True,
                )
                top_role = (
                    member_in_guild.top_role.mention
                    if member_in_guild.top_role != target_guild.default_role
                    else "`No roles`"
                )
                report_embed.add_field(
                    name="🏷️  Highest Role",
                    value=top_role,
                    inline=True,
                )
            else:
                report_embed.add_field(
                    name="⚠️  Server Status",
                    value="`Not currently in this server`",
                    inline=True,
                )

            # Reason
            report_embed.add_field(
                name="📝  Reason / Description",
                value=report_reason[:1024],
                inline=False,
            )

            # Evidence summary
            total_attachments = sum(len(m.attachments) for m in evidence_msgs)
            text_pieces       = sum(1 for m in evidence_msgs if m.content.strip())
            report_embed.add_field(
                name="🗂️  Evidence Summary",
                value=(
                    f"`{len(evidence_msgs)}` message(s) · "
                    f"`{text_pieces}` text piece(s) · "
                    f"`{total_attachments}` attachment(s)"
                ),
                inline=False,
            )

            # Set first image as the embed image for a quick visual preview
            for ev_msg in evidence_msgs:
                for att in ev_msg.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        report_embed.set_image(url=att.url)
                        break
                else:
                    continue
                break

            report_embed.set_footer(
                text=f"Report ID: {report_id}  ·  Submitted via DM  ·  {target_guild.name}"
            )

            # ── Create Thread & Dispatch logs ────────────────────────────────
            try:
                report_thread = await report_channel.create_thread(
                    name=report_id,
                    type=discord.ChannelType.public_thread,
                    reason=f"System generated report log thread for {report_id}."
                )
            except discord.HTTPException as e:
                return await dm.send(
                    embed=err(
                        f"Failed to isolate report context inside **{target_guild.name}** thread layout. "
                        f"Permissions missing? Error details: `{e}`"
                    )
                )

            # Post the main report card straight into the active thread target
            await report_thread.send(embed=report_embed)

            # ── Evidence follow-ups inside the dedicated thread ───────────────

            if evidence_msgs:
                ev_header = discord.Embed(
                    title=f"📎  Evidence — {report_id}",
                    description=(
                        f"Filed by **{author}** (`{author.id}`) "
                        f"against **{reported_user}** (`{reported_user.id}`)\n"
                        f"**{len(evidence_msgs)}** evidence message(s) below:"
                    ),
                    colour=COL_PURPLE,
                    timestamp=now,
                )
                await report_thread.send(embed=ev_header)

                for idx, ev_msg in enumerate(evidence_msgs, 1):
                    ev_embed = discord.Embed(
                        title=f"Evidence {idx} / {len(evidence_msgs)}",
                        colour=COL_DARK,
                        timestamp=ev_msg.created_at,
                    )
                    ev_embed.set_footer(
                        text=f"Sent by {author}  ·  {report_id}",
                        icon_url=author.display_avatar.url,
                    )

                    if ev_msg.content.strip():
                        ev_embed.description = ev_msg.content[:4096]

                    files_to_send: list[discord.File] = []
                    failed_urls:   list[str]           = []

                    for att in ev_msg.attachments:
                        try:
                            files_to_send.append(await att.to_file(use_cached=True))
                        except Exception:
                            failed_urls.append(att.url)

                    if failed_urls:
                        ev_embed.add_field(
                            name="⚠️  Could Not Re-Upload",
                            value="\n".join(failed_urls),
                            inline=False,
                        )

                    if files_to_send:
                        await report_thread.send(embed=ev_embed, files=files_to_send)
                    else:
                        await report_thread.send(embed=ev_embed)

            # ── Notify reporter of success ────────────────────────────────────

            success_embed = discord.Embed(
                title="✅  Report Successfully Submitted",
                description=(
                    f"Your report has been delivered to the **{target_guild.name}** staff.\n\n"
                    f"🆔  **Report ID:** `{report_id}`\n\n"
                    "Staff will review your report as soon as possible. "
                    "Please do **not** repeatedly submit reports about the same incident. "
                    "Doing so may result in your report being dismissed."
                ),
                colour=COL_SUCCESS,
                timestamp=now,
            )
            success_embed.add_field(
                name="🎯  Reported User",
                value=f"`{reported_user}` · `{reported_user.id}`",
                inline=True,
            )
            success_embed.add_field(
                name="📎  Evidence Pieces",
                value=str(len(evidence_msgs)),
                inline=True,
            )
            success_embed.set_footer(text="Thank you for helping keep the server safe.")
            await dm.send(embed=success_embed)

        # ── Graceful exits ────────────────────────────────────────────────────

        except ExitReport:
            await dm.send(
                embed=warn_emb(
                    "Report cancelled. No information has been submitted. "
                    "You can start a new report at any time with `!report`."
                )
            )

        except TimeoutReport:
            await dm.send(
                embed=err(
                    "Your report was cancelled because you didn't respond in time. "
                    "Please use `!report` to start again."
                )
            )

        except discord.Forbidden:
            # Can't message the reporter — nothing we can do
            pass

        except Exception as exc:
            try:
                await dm.send(
                    embed=err(
                        f"An unexpected error occurred while processing your report: `{exc}`\n"
                        "Please try again or contact a server admin."
                    )
                )
            except Exception:
                pass
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: ask for and resolve a user ID
    # ─────────────────────────────────────────────────────────────────────────

    async def _ask_for_user(
        self, dm: discord.DMChannel, author: discord.User
    ) -> discord.User:
        """Loop until the author provides a valid user ID or mention."""
        while True:
            resp    = await self._wait_for_message(author)
            raw     = resp.content.strip().lstrip("<@!").rstrip(">")
            try:
                uid  = int(raw)
            except ValueError:
                await dm.send(
                    embed=err(
                        "That doesn't look like a valid User ID. "
                        "Please send only the **numeric ID** (e.g. `123456789012345678`)."
                    )
                )
                continue

            try:
                user = await self.bot.fetch_user(uid)
                return user
            except discord.NotFound:
                await dm.send(
                    embed=err(
                        f"No Discord user found with ID `{uid}`. "
                        "Please double-check the ID and try again."
                    )
                )
            except discord.HTTPException:
                await dm.send(
                    embed=err(
                        "Failed to look up that user — Discord might be having issues. "
                        "Please try again."
                    )
                )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: confirm reported user identity
    # ─────────────────────────────────────────────────────────────────────────

    async def _confirm_user(
        self,
        dm: discord.DMChannel,
        author: discord.User,
        user: discord.User,
    ) -> discord.User:
        """Show the resolved user and ask the reporter to confirm before continuing."""
        while True:
            confirm = discord.Embed(
                title="📋  Confirm — Is this the right person?",
                description=(
                    f"**Name:** {user}\n"
                    f"**ID:** `{user.id}`\n"
                    f"**Account created:** {discord.utils.format_dt(user.created_at, 'R')}\n\n"
                    "Reply `yes` to continue, or `no` to enter a different User ID."
                ),
                colour=COL_CONFIRM,
            )
            confirm.set_thumbnail(url=user.display_avatar.url)
            confirm.set_footer(text="Type !exit to cancel")
            await dm.send(embed=confirm)

            resp   = await self._wait_for_message(author)
            answer = resp.content.strip().lower()

            if answer in ("yes", "y", "yep", "yeah"):
                return user
            elif answer in ("no", "n", "nope"):
                await dm.send(embed=info_emb("Okay — please send the correct User ID."))
                user = await self._ask_for_user(dm, author)
                # loop back to confirm again
            else:
                await dm.send(embed=err("Please reply with **`yes`** or **`no`**."))


# ── Setup ─────────────────────────────────────────────────────────────────────

async def setup(bot):
    await bot.add_cog(Reporter(bot))