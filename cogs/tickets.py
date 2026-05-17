import discord
import json
import os
import asyncio
import io
import datetime
import logging
import chat_exporter

from discord.ext import commands
from discord import app_commands
from utils import permissions
from utils.default import CustomContext
from utils.data import DiscordBot, ACCENT_COLOUR

log = logging.getLogger("bot.tickets")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tickets.json")

def _load() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save(data: dict):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)

# ── Views ──────────────────────────────────────────────────────────────────────

class TicketActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_action_close", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        cog = interaction.client.get_cog("Tickets")
        if cog:
            await cog.close_ticket(interaction.guild, interaction.channel, interaction.user)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_action_claim", emoji="👋")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = _load()
        gkey = str(interaction.guild.id)
        if gkey not in data:
            return await interaction.response.send_message("❌ Configuration error.", ephemeral=True)
        
        support_role_id = data[gkey].get("support_role")
        has_access = interaction.user.guild_permissions.manage_channels
        if support_role_id:
            role = interaction.guild.get_role(support_role_id)
            if role and role in interaction.user.roles:
                has_access = True
                
        if not has_access:
            return await interaction.response.send_message("❌ You do not have permission to claim this ticket.", ephemeral=True)
            
        # Update UI
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        button.style = discord.ButtonStyle.secondary
        msg = interaction.message
        
        embed = msg.embeds[0] if msg.embeds else None
        if embed:
            embed.color = discord.Colour.orange()
            embed.set_footer(text=f"Claimed by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            
        await interaction.response.edit_message(embed=embed, view=self)
        try:
            await interaction.channel.edit(name=f"claimed-{interaction.user.name[:10]}")
        except discord.HTTPException:
            pass
        await interaction.channel.send(f"🛡️ {interaction.user.mention} will be assisting you shortly!")


class TicketPanelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="General Support", description="Help with general server queries", emoji="❓", value="general"),
            discord.SelectOption(label="Report a User", description="Report rule-breaking behavior", emoji="⚠️", value="report"),
            discord.SelectOption(label="Billing/Donations", description="Ask about purchases or donations", emoji="💳", value="billing"),
            discord.SelectOption(label="Other", description="Anything else that does not fit", emoji="💬", value="other")
        ]
        super().__init__(placeholder="Select the reason for your ticket...", min_values=1, max_values=1, options=options, custom_id="ticket_panel_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        reason = self.values[0]
        cog = interaction.client.get_cog("Tickets")
        if cog:
            await cog.create_ticket(interaction, reason)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketPanelSelect())


# ── Cog ────────────────────────────────────────────────────────────────────────

class Tickets(commands.Cog):
    """🎟️ Ticket system with HTML transcripts and dropdown support."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot

    async def cog_load(self):
        # Register persistent views
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketActionView())

    async def create_ticket(self, interaction: discord.Interaction, reason: str):
        guild = interaction.guild
        user = interaction.user
        gkey = str(guild.id)
        
        data = _load()
        if gkey not in data or "support_role" not in data[gkey]:
            return await interaction.followup.send("❌ Tickets are not configured on this server.", ephemeral=True)
            
        cfg = data[gkey]
        open_tickets = cfg.setdefault("open_tickets", {})
        
        # Check limit
        if str(user.id) in open_tickets:
            existing_channel = guild.get_channel(open_tickets[str(user.id)])
            if existing_channel:
                return await interaction.followup.send(f"❌ You already have an open ticket: {existing_channel.mention}", ephemeral=True)
        
        # Determine category
        cat_id = cfg.get("category_id")
        category = guild.get_channel(cat_id) if cat_id else None
        
        if not category:
            # Auto-create
            try:
                category = await guild.create_category("Tickets")
                cfg["category_id"] = category.id
                _save(data)
            except discord.Forbidden:
                return await interaction.followup.send("❌ I do not have permission to manage channels to create tickets.", ephemeral=True)

        support_role = guild.get_role(cfg["support_role"])
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_permissions=True, read_message_history=True),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, read_message_history=True)
        }
        
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, read_message_history=True)
            
        try:
            channel_name = f"ticket-{user.name}"
            ticket_channel = await category.create_text_channel(name=channel_name, overwrites=overwrites, topic=f"{user.id}")
        except discord.Forbidden:
            return await interaction.followup.send("❌ Permission error creating the ticket channel.", ephemeral=True)
            
        # Save state
        cfg["open_tickets"][str(user.id)] = ticket_channel.id
        _save(data)
        
        # Send greeting
        reason_label = {"general": "General Support", "report": "Report User", "billing": "Billing", "other": "Other"}.get(reason, "Other")
        
        embed = discord.Embed(
            title="🎟️ Ticket Opened",
            description=f"Welcome {user.mention}!\n\nA support member will be with you shortly. Please describe your issue in detail.\n\n**Reason:** `{reason_label}`",
            colour=ACCENT_COLOUR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Click Claim Ticket to assign this to yourself.")
        
        mention_str = user.mention
        if support_role:
            mention_str += f" {support_role.mention}"
            
        await ticket_channel.send(content=mention_str, embed=embed, view=TicketActionView())
        await interaction.followup.send(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)


    async def close_ticket(self, guild: discord.Guild, channel: discord.TextChannel, closer: discord.Member):
        data = _load()
        gkey = str(guild.id)
        if gkey not in data:
            return
            
        cfg = data[gkey]
        open_tickets = cfg.setdefault("open_tickets", {})
        
        # Find owner
        owner_id_str = None
        for uid, cid in open_tickets.items():
            if cid == channel.id:
                owner_id_str = uid
                break
                
        if owner_id_str is None:
            # Fallback to channel topic if not found in db
            owner_id_str = channel.topic if channel.topic and channel.topic.isdigit() else None
            
        if owner_id_str:
            if owner_id_str in open_tickets:
                del open_tickets[owner_id_str]
            _save(data)
            
        await channel.send("🔒 Generating transcript and closing ticket...")

        # Generate Transcript
        transcript_bytes = None
        try:
            transcript = await chat_exporter.export(channel, tz_info="UTC")
            if transcript:
                transcript_file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{channel.name}.html")
                
                # Send to log channel
                log_channel_id = cfg.get("log_channel")
                log_ch = guild.get_channel(log_channel_id) if log_channel_id else None
                
                owner = guild.get_member(int(owner_id_str)) if owner_id_str else None
                
                log_embed = discord.Embed(
                    title="🔒 Ticket Closed",
                    description=f"Ticket **{channel.name}** was closed by {closer.mention}.",
                    colour=discord.Colour.orange(),
                    timestamp=discord.utils.utcnow()
                )
                if owner:
                    log_embed.add_field(name="Ticket Owner", value=f"{owner.mention} (`{owner.id}`)")
                    
                if log_ch:
                    await log_ch.send(embed=log_embed, file=transcript_file)
        except Exception as e:
            log.error(f"Failed to generate transcript: {e}", exc_info=True)
            
        # Delete channel
        try:
            await channel.delete(reason=f"Ticket closed by {closer}")
        except discord.Forbidden:
            pass


    @commands.group(invoke_without_command=True)
    async def ticket(self, ctx: CustomContext):
        """Ticket management commands."""
        await ctx.send_help(ctx.command)

    @ticket.command(name="setup")
    @commands.guild_only()
    @permissions.has_permissions(manage_guild=True)
    async def ticket_setup(self, ctx: CustomContext, channel: discord.TextChannel, support_role: discord.Role, log_channel: discord.TextChannel = None):
        """Setup the ticket system.
        Usage: !ticket setup #panel_channel @support_role [#log_channel]
        """
        data = _load()
        gkey = str(ctx.guild.id)
        if gkey not in data:
            data[gkey] = {}
            
        data[gkey]["support_role"] = support_role.id
        data[gkey]["log_channel"] = log_channel.id if log_channel else None
        _save(data)
        
        embed = discord.Embed(
            title="🎟️ Support Tickets",
            description="Welcome to the support center. Please select the reason for your ticket below to speak with staff.",
            colour=ACCENT_COLOUR
        )
        embed.set_footer(text="A private channel will be created for you.")
        
        await channel.send(embed=embed, view=TicketPanelView())
        
        success_msg = f"✅ Ticket panel sent to {channel.mention}. Support role set to {support_role.mention}."
        if log_channel:
            success_msg += f" Transcripts will be logged to {log_channel.mention}."
        await ctx.send(success_msg)

    @ticket.command(name="add")
    @commands.guild_only()
    async def ticket_add(self, ctx: CustomContext, member: discord.Member):
        """Add a user to the current ticket."""
        # Minimal verification: is it a ticket?
        data = _load()
        is_ticket = False
        gkey = str(ctx.guild.id)
        if gkey in data:
            tickets = data[gkey].get("open_tickets", {})
            if ctx.channel.id in tickets.values():
                is_ticket = True
                
        # Also simple check if "ticket-" in name
        if not is_ticket and not ctx.channel.name.startswith("ticket-") and not ctx.channel.name.startswith("claimed-"):
            return await ctx.send("❌ This command can only be used inside a ticket channel.")
            
        try:
            await ctx.channel.set_permissions(member, read_messages=True, send_messages=True, read_message_history=True)
            await ctx.send(f"✅ Added {member.mention} to the ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I lack permissions to modify channel overwrites.")

    @ticket.command(name="remove")
    @commands.guild_only()
    async def ticket_remove(self, ctx: CustomContext, member: discord.Member):
        """Remove a user from the current ticket."""
        if not ctx.channel.name.startswith("ticket-") and not ctx.channel.name.startswith("claimed-"):
            return await ctx.send("❌ This command can only be used inside a ticket channel.")
            
        try:
            await ctx.channel.set_permissions(member, overwrite=None)
            await ctx.send(f"✅ Removed {member.mention} from the ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I lack permissions to modify channel overwrites.")

    @ticket.command(name="close")
    @commands.guild_only()
    async def ticket_cmd_close(self, ctx: CustomContext):
        """Close the current ticket."""
        if not ctx.channel.name.startswith("ticket-") and not ctx.channel.name.startswith("claimed-"):
            return await ctx.send("❌ This command can only be used inside a ticket channel.")
        # Acknowledge before close_ticket deletes the channel so the user
        # always sees a response, even if transcript generation is slow.
        try:
            await ctx.message.add_reaction("🔒")
        except discord.HTTPException:
            pass
        await self.close_ticket(ctx.guild, ctx.channel, ctx.author)


async def setup(bot):
    await bot.add_cog(Tickets(bot))