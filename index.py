"""
index.py — Discord bot entry point.
"""

import discord
import os
import sys
import logging
from dotenv import load_dotenv

# Ensure project root is on path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Load .env BEFORE importing anything that reads env vars
load_dotenv(os.path.join(BASE_DIR, ".env"))

from utils.config import Config
from utils.data import DiscordBot, HelpFormat

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

# ── CONFIG — reads from .env ───────────────────────────────────────────────────
_owner_ids_raw = os.environ.get("DISCORD_OWNER_IDS", "")
_owner_ids = [int(x.strip()) for x in _owner_ids_raw.split(",") if x.strip()]
if not _owner_ids:
    log.warning("DISCORD_OWNER_IDS is not set — no owner-only commands will work!")

config = Config(
    discord_token=os.environ.get("DISCORD_TOKEN", ""),
    discord_prefix="!",
    discord_owner_ids=_owner_ids,
    discord_join_message="Welcome to the server! 👋",
    discord_activity_name="with code",
    discord_activity_type="playing",
    discord_status_type="online",
    discord_autorole_id=None,
)

log.info("Initialising bot...")

# ── BOT ────────────────────────────────────────────────────────────────────────
bot = DiscordBot(
    config=config,
    command_prefix=config.discord_prefix,
    prefix=config.discord_prefix,
    command_attrs=dict(hidden=True),
    help_command=HelpFormat(),
    allowed_mentions=discord.AllowedMentions(
        everyone=False,
        roles=False,
        users=True,
    ),
    intents=discord.Intents(
        guilds=True,
        members=True,
        moderation=True,
        messages=True,
        reactions=True,
        presences=True,
        message_content=True,
        voice_states=True,
    ),
)

os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")


# ── RUN ────────────────────────────────────────────────────────────────────────
try:
    bot.run(config.discord_token, log_handler=None)
except discord.LoginFailure as e:
    log.critical(f"Login failed — invalid token: {e}")
    sys.exit(1)
except Exception as e:
    log.exception(f"Unexpected error during bot.run: {e}")
    sys.exit(1)
