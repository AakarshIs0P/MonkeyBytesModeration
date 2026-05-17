"""
cogs/ai.py  --  AI Chat powered by Groq  +  OpenRouter (alt AI)
=================================================================

Groq commands (prefix !):
  !ask <question>         One-shot Q&A — no memory.  Attach image/file for context.
  !chat <message>         Conversational — remembers last N exchanges per channel.  Attach image/file.
  !aiclear                Clear this channel's conversation history.
  !aimodel [model]        Show or switch the active Groq model (owners only).
  !aisystem [prompt]      View or update the system prompt / personality (owners only).
  !aimemory [n]           View or set how many message pairs !chat remembers (owners only).
  !aistats                Session usage stats — requests, tokens, errors.

OpenRouter commands (prefix !c):
  !cask <question>        One-shot Q&A via OpenRouter.  Attach image/file for context.
  !cchat <message>        Conversational via OpenRouter — per-channel memory.  Attach image/file.
  !cclear                 Clear OpenRouter conversation history for this channel.
  !caimodel [model]       Show or switch the active OpenRouter model (owners only).
  !caisystem [prompt]     View or update the OpenRouter system prompt (owners only).
  !caimemory [n]          View or set OpenRouter memory depth (owners only).
  !caistats               OpenRouter session usage stats.

File / Image support (both AIs):
  Attach any image (PNG, JPEG, GIF, WEBP) to have the AI vision-analyse it.
  Attach a text file (.txt, .py, .js, .md, .json, .csv, .log, …) to include its
  contents as extra context.  Files larger than 32 KB are truncated with a notice.
"""

import asyncio
import base64
import datetime
import os
import logging
import time
from collections import defaultdict, deque
from io import BytesIO

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils.data import DiscordBot
from utils.permissions import OWNERS

log = logging.getLogger("bot.ai")

# ── Groq config ────────────────────────────────────────────────────────────────

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

GROQ_AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "gemma2-9b-it",
    "mixtral-8x7b-32768",
    # Vision-capable models
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview",
]

# Models that support image input on Groq
GROQ_VISION_MODELS = {
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview",
}

# ── OpenRouter config ──────────────────────────────────────────────────────────

OR_API_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
OR_API_URL  = "https://openrouter.ai/api/v1/chat/completions"
OR_SITE_URL = "https://discord.gg"   # shown in OR dashboard
OR_APP_NAME = "DiscordBot"

OR_DEFAULT_MODEL = "openai/gpt-4o"

OR_AVAILABLE_MODELS = [
    # Vision-capable (image support)
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-4-turbo",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-pro-1.5",
    "google/gemini-flash-1.5",
    # Text-only but powerful
    "meta-llama/llama-3.3-70b-instruct",
    "mistralai/mixtral-8x7b-instruct",
    "deepseek/deepseek-chat",
    "qwen/qwen-2.5-72b-instruct",
]

# Models on OpenRouter that support vision (image input)
OR_VISION_MODELS = {
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-4-turbo",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-pro-1.5",
    "google/gemini-flash-1.5",
}

# ── Shared config ──────────────────────────────────────────────────────────────

DEFAULT_MAX_HISTORY = 10     # user+assistant pairs kept per channel
MAX_TOKENS          = 1024
TIMEOUT_S           = 45
MAX_FILE_BYTES      = 32_768  # 32 KB text-file cap

DEFAULT_SYSTEM = (
    "You are a helpful, friendly assistant living inside a Discord server. "
    "Keep responses concise and clear. Use Discord markdown where it helps "
    "(bold, italics, inline code, code blocks). "
    "Never exceed 1800 characters in a single reply."
)

# ── Text file extensions we'll try to read ────────────────────────────────────
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml",
    ".yml", ".toml", ".csv", ".log", ".sh", ".bash", ".html", ".css",
    ".xml", ".ini", ".cfg", ".env", ".rs", ".go", ".java", ".c", ".cpp",
    ".h", ".rb", ".php", ".lua", ".sql", ".r", ".kt", ".swift",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# ── Colours / icons ────────────────────────────────────────────────────────────

COL_AI      = discord.Colour.from_str("#7289DA")   # Groq / main
COL_OR      = discord.Colour.from_str("#10A37F")   # OpenRouter green
COL_SUCCESS = discord.Colour.from_str("#1E8449")
COL_ERROR   = discord.Colour.from_str("#C0392B")
COL_MUTED   = discord.Colour.from_str("#566573")
COL_WARN    = discord.Colour.from_str("#E67E22")

ICO_AI   = "🤖"
ICO_OR   = "🌐"
ICO_OK   = "✅"
ICO_ERR  = "❌"
ICO_STAT = "📊"
ICO_IMG  = "🖼️"
ICO_FILE = "📄"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _base_embed(title: str, description: str, colour: discord.Colour) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour, timestamp=_now())


def _err(text: str) -> discord.Embed:
    return _base_embed(f"{ICO_ERR}  Error", text, COL_ERROR)


# ── Attachment helpers ─────────────────────────────────────────────────────────

def _attachment_ext(att: discord.Attachment) -> str:
    """Return lowercased file extension including the dot, e.g. '.png'."""
    import os
    return os.path.splitext(att.filename.lower())[1]


async def _read_attachment_bytes(att: discord.Attachment, session: aiohttp.ClientSession) -> bytes:
    """Download an attachment and return raw bytes."""
    async with session.get(att.url) as resp:
        resp.raise_for_status()
        return await resp.read()


async def _build_image_part(att: discord.Attachment, session: aiohttp.ClientSession) -> dict:
    """
    Return an OpenAI-style image_url content part for a Discord image attachment.
    Works for both Groq and OpenRouter.
    """
    raw   = await _read_attachment_bytes(att, session)
    b64   = base64.b64encode(raw).decode()
    ext   = _attachment_ext(att).lstrip(".")
    mime  = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


async def _build_file_text(att: discord.Attachment, session: aiohttp.ClientSession) -> str:
    """
    Download a text-file attachment and return its content as a string,
    truncated to MAX_FILE_BYTES with a notice if needed.
    """
    raw = await _read_attachment_bytes(att, session)
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")

    truncated = False
    if len(text.encode()) > MAX_FILE_BYTES:
        text      = text[: MAX_FILE_BYTES].rsplit("\n", 1)[0]
        truncated = True

    header = f"[Attached file: {att.filename}]\n"
    footer = "\n[...file truncated to 32 KB]" if truncated else ""
    return header + text + footer


async def _process_attachments(
    attachments: list[discord.Attachment],
    text_content: str,
    session: aiohttp.ClientSession,
    *,
    supports_vision: bool,
) -> tuple[list[dict] | str, list[str]]:
    """
    Process a list of Discord attachments and build a content payload.

    Returns:
        (content, notices)
        content  — either a plain string (no images) or a list of content parts
        notices  — list of human-readable notes about what was processed
    """
    text_parts: list[str]  = [text_content] if text_content else []
    image_parts: list[dict] = []
    notices: list[str]      = []

    for att in attachments:
        ext = _attachment_ext(att)

        if ext in IMAGE_EXTENSIONS:
            if supports_vision:
                try:
                    part = await _build_image_part(att, session)
                    image_parts.append(part)
                    notices.append(f"{ICO_IMG} Image attached: `{att.filename}`")
                except Exception as e:
                    notices.append(f"{ICO_ERR} Could not load image `{att.filename}`: {e}")
            else:
                notices.append(
                    f"⚠️ Image `{att.filename}` ignored — current model doesn't support vision."
                )

        elif ext in TEXT_EXTENSIONS or att.filename.lower().endswith(tuple(TEXT_EXTENSIONS)):
            try:
                file_text = await _build_file_text(att, session)
                text_parts.append(file_text)
                notices.append(f"{ICO_FILE} File attached: `{att.filename}`")
            except Exception as e:
                notices.append(f"{ICO_ERR} Could not read file `{att.filename}`: {e}")

        else:
            notices.append(
                f"⚠️ Unsupported attachment `{att.filename}` — "
                "only images and text files are supported."
            )

    combined_text = "\n\n".join(text_parts)

    if image_parts:
        # Build a multipart content list
        content: list[dict] = [{"type": "text", "text": combined_text}] if combined_text else []
        content.extend(image_parts)
    else:
        content = combined_text  # plain string — compatible with all models

    return content, notices


# ══════════════════════════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════════════════════════

class AI(commands.Cog):
    """AI chat powered by Groq + OpenRouter — fast LLM responses in Discord."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot

        # ── Groq state ──────────────────────────────────────────────────────
        self.groq_model:         str = GROQ_DEFAULT_MODEL
        self.groq_system_prompt: str = DEFAULT_SYSTEM
        self.groq_max_history:   int = DEFAULT_MAX_HISTORY
        self._groq_history: dict[int, deque] = defaultdict(deque)
        self._groq_stats = {
            "requests": 0, "tokens_prompt": 0,
            "tokens_completion": 0, "errors": 0, "started": time.time(),
        }

        # ── OpenRouter state ─────────────────────────────────────────────────
        self.or_model:         str = OR_DEFAULT_MODEL
        self.or_system_prompt: str = DEFAULT_SYSTEM
        self.or_max_history:   int = DEFAULT_MAX_HISTORY
        self._or_history: dict[int, deque] = defaultdict(deque)
        self._or_stats = {
            "requests": 0, "tokens_prompt": 0,
            "tokens_completion": 0, "errors": 0, "started": time.time(),
        }

        # Shared aiohttp session (lazy)
        self._session: aiohttp.ClientSession | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            )
        return self._session

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Groq API call ──────────────────────────────────────────────────────────

    async def _call_groq(
        self,
        messages: list[dict],
        *,
        max_tokens: int = MAX_TOKENS,
    ) -> tuple[str, dict]:
        session = await self._get_session()
        payload = {
            "model":       self.groq_model,
            "messages":    [{"role": "system", "content": self.groq_system_prompt}] + messages,
            "max_tokens":  max_tokens,
            "temperature": 0.7,
        }
        self._groq_stats["requests"] += 1
        try:
            async with session.post(
                GROQ_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    self._groq_stats["errors"] += 1
                    msg = data.get("error", {}).get("message", f"HTTP {resp.status}")
                    raise RuntimeError(msg)
                content = data["choices"][0]["message"]["content"].strip()
                usage   = data.get("usage", {})
                self._groq_stats["tokens_prompt"]     += usage.get("prompt_tokens", 0)
                self._groq_stats["tokens_completion"] += usage.get("completion_tokens", 0)
                return content, usage
        except aiohttp.ClientError as e:
            self._groq_stats["errors"] += 1
            raise RuntimeError(f"Network error: {e}") from e
        except asyncio.TimeoutError:
            self._groq_stats["errors"] += 1
            raise RuntimeError(f"Request timed out after {TIMEOUT_S}s.") from None

    # ── OpenRouter API call ────────────────────────────────────────────────────

    async def _call_openrouter(
        self,
        messages: list[dict],
        *,
        max_tokens: int = MAX_TOKENS,
    ) -> tuple[str, dict]:
        session = await self._get_session()
        payload = {
            "model":       self.or_model,
            "messages":    [{"role": "system", "content": self.or_system_prompt}] + messages,
            "max_tokens":  max_tokens,
            "temperature": 0.7,
        }
        self._or_stats["requests"] += 1
        try:
            async with session.post(
                OR_API_URL,
                json=payload,
                headers={
                    "Authorization":    f"Bearer {OR_API_KEY}",
                    "Content-Type":     "application/json",
                    "HTTP-Referer":     OR_SITE_URL,
                    "X-Title":          OR_APP_NAME,
                },
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    self._or_stats["errors"] += 1
                    msg = data.get("error", {}).get("message", f"HTTP {resp.status}")
                    raise RuntimeError(msg)
                content = data["choices"][0]["message"]["content"].strip()
                usage   = data.get("usage", {})
                self._or_stats["tokens_prompt"]     += usage.get("prompt_tokens", 0)
                self._or_stats["tokens_completion"] += usage.get("completion_tokens", 0)
                return content, usage
        except aiohttp.ClientError as e:
            self._or_stats["errors"] += 1
            raise RuntimeError(f"Network error: {e}") from e
        except asyncio.TimeoutError:
            self._or_stats["errors"] += 1
            raise RuntimeError(f"Request timed out after {TIMEOUT_S}s.") from None

    # ── History helpers ────────────────────────────────────────────────────────

    def _push_history(self, history: dict[int, deque], max_h: int, channel_id: int, role: str, content):
        q = history[channel_id]
        q.append({"role": role, "content": content})
        while len(q) > max_h * 2:
            q.popleft()

    def _get_history(self, history: dict[int, deque], channel_id: int) -> list[dict]:
        return list(history[channel_id])

    def _clear_history(self, history: dict[int, deque], channel_id: int):
        history[channel_id].clear()

    # ── Response helpers ───────────────────────────────────────────────────────

    async def _thinking(self, ctx: commands.Context, icon: str = ICO_AI, colour: discord.Colour = COL_MUTED) -> discord.Message:
        embed = discord.Embed(description=f"{icon}  *Thinking...*", colour=colour)
        return await ctx.send(embed=embed)

    async def _edit_reply(
        self,
        msg: discord.Message,
        author: discord.Member | discord.User,
        question: str,
        answer: str,
        usage: dict,
        *,
        subtitle: str = "",
        model: str = "",
        icon: str = ICO_AI,
        colour: discord.Colour = COL_AI,
        notices: list[str] | None = None,
    ):
        if len(answer) > 3900:
            answer = answer[:3897] + "…"

        title = f"{icon}  AI" + (f"  —  {subtitle}" if subtitle else "")
        embed = discord.Embed(title=title, colour=colour, timestamp=_now())
        embed.add_field(name="Question", value=f"> {question[:512]}", inline=False)
        embed.add_field(
            name="Answer",
            value=answer[:1021] + "\u2026" if len(answer) > 1021 else answer,
            inline=False,
        )
        if notices:
            embed.add_field(name="Attachments", value="\n".join(notices), inline=False)
        embed.set_author(name=str(author), icon_url=author.display_avatar.url)
        embed.set_footer(
            text=(
                f"Model: {model or 'unknown'}  •  "
                f"{usage.get('prompt_tokens', '?')} in / "
                f"{usage.get('completion_tokens', '?')} out tokens"
            )
        )
        await msg.edit(embed=embed)

    def _stats_embed(self, stats: dict, model: str, history: dict, max_h: int, icon: str, colour: discord.Colour, label: str) -> discord.Embed:
        elapsed = int(time.time() - stats["started"])
        d, r    = divmod(elapsed, 86400)
        h, r    = divmod(r, 3600)
        m, s    = divmod(r, 60)
        uptime  = (
            f"{d}d {h}h {m}m {s}s" if d else
            f"{h}h {m}m {s}s"      if h else
            f"{m}m {s}s"
        )
        total_tok = stats["tokens_prompt"] + stats["tokens_completion"]
        active_ch = sum(1 for q in history.values() if q)
        embed = _base_embed(f"{ICO_STAT}  {label} Session Stats", "", colour)
        embed.add_field(name="Model",             value=f"`{model}`",                               inline=True)
        embed.add_field(name="Uptime",            value=f"`{uptime}`",                              inline=True)
        embed.add_field(name="Active Channels",   value=f"`{active_ch}`",                           inline=True)
        embed.add_field(name="Total Requests",    value=f"`{stats['requests']}`",                   inline=True)
        embed.add_field(name="Errors",            value=f"`{stats['errors']}`",                     inline=True)
        embed.add_field(name="Memory Depth",      value=f"`{max_h}` pairs",                         inline=True)
        embed.add_field(name="Prompt Tokens",     value=f"`{stats['tokens_prompt']:,}`",            inline=True)
        embed.add_field(name="Completion Tokens", value=f"`{stats['tokens_completion']:,}`",        inline=True)
        embed.add_field(name="Total Tokens",      value=f"`{total_tok:,}`",                         inline=True)
        embed.set_footer(text="Stats reset on bot restart")
        return embed

    # ══════════════════════════════════════════════════════════════════════════
    #  GROQ COMMANDS
    # ══════════════════════════════════════════════════════════════════════════

    # ── !ask ──────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="ask", description="Ask the AI a one-shot question — no memory. Attach images or files!")
    @commands.guild_only()
    @commands.cooldown(1, 5.0, commands.BucketType.user)
    @app_commands.describe(question="What do you want to ask?")
    async def ask(self, ctx: commands.Context, *, question: str = ""):
        """Ask the AI a single question with no conversation memory.
Attach an image or text file to include it as context.
Supports slash: /ask"""
        question = question.strip()
        if not question and not ctx.message.attachments:
            return await ctx.send(embed=_err("Please include a question or attachment."), ephemeral=True)

        session = await self._get_session()
        supports_vision = self.groq_model in GROQ_VISION_MODELS
        content, notices = await _process_attachments(
            ctx.message.attachments, question, session, supports_vision=supports_vision
        )

        msg = await self._thinking(ctx)
        try:
            answer, usage = await self._call_groq([{"role": "user", "content": content}])
            await self._edit_reply(
                msg, ctx.author, question or "(see attachment)", answer, usage,
                subtitle="Ask", model=self.groq_model, notices=notices or None,
            )
        except RuntimeError as e:
            await msg.edit(embed=_err(str(e)))
        except Exception as e:
            log.exception("Unexpected error in !ask")
            await msg.edit(embed=_err(f"Unexpected error: {e}"))

    # ── !chat ─────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="chat", description="Chat with the AI — remembers recent messages. Attach images or files!")
    @commands.guild_only()
    @commands.cooldown(1, 5.0, commands.BucketType.user)
    @app_commands.describe(message="Your message to the AI")
    async def chat(self, ctx: commands.Context, *, message: str = ""):
        """Chat with the AI. Remembers the last N exchanges in this channel.
Attach an image or text file to include it as context.
Use !aiclear to wipe history. Supports slash: /chat"""
        message = message.strip()
        if not message and not ctx.message.attachments:
            return await ctx.send(embed=_err("Please include a message or attachment."), ephemeral=True)

        session = await self._get_session()
        supports_vision = self.groq_model in GROQ_VISION_MODELS
        content, notices = await _process_attachments(
            ctx.message.attachments, message, session, supports_vision=supports_vision
        )

        history = self._get_history(self._groq_history, ctx.channel.id)
        history.append({"role": "user", "content": content})

        msg = await self._thinking(ctx)
        try:
            answer, usage = await self._call_groq(history)
            self._push_history(self._groq_history, self.groq_max_history, ctx.channel.id, "user", content)
            self._push_history(self._groq_history, self.groq_max_history, ctx.channel.id, "assistant", answer)
            pairs = len(self._groq_history[ctx.channel.id]) // 2
            await self._edit_reply(
                msg, ctx.author, message or "(see attachment)", answer, usage,
                subtitle=f"Chat  (memory: {pairs}/{self.groq_max_history})",
                model=self.groq_model, notices=notices or None,
            )
        except RuntimeError as e:
            await msg.edit(embed=_err(str(e)))
        except Exception as e:
            log.exception("Unexpected error in !chat")
            await msg.edit(embed=_err(f"Unexpected error: {e}"))

    # ── !aiclear ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="aiclear", description="Clear the Groq AI conversation history for this channel.")
    @commands.guild_only()
    async def aiclear(self, ctx: commands.Context):
        """Wipe the !chat memory for this channel. Affects everyone in the channel."""
        count = len(self._groq_history[ctx.channel.id])
        self._clear_history(self._groq_history, ctx.channel.id)
        embed = _base_embed(
            f"{ICO_OK}  Conversation Cleared",
            f"Removed **{count}** message{'s' if count != 1 else ''} from this channel's Groq AI memory.",
            COL_SUCCESS,
        )
        embed.set_footer(text=f"Cleared by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !aimodel ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="aimodel", description="Show or switch the active Groq model.")
    @commands.guild_only()
    @app_commands.describe(model="Model to switch to (leave blank to see options)")
    async def aimodel(self, ctx: commands.Context, *, model: str = None):
        """View available Groq models or switch the active one. Switching requires bot owner."""
        if model is None:
            lines = "\n".join(
                f"{'▶ ' if m == self.groq_model else '  '}`{m}`"
                + (" 👁️" if m in GROQ_VISION_MODELS else "")
                for m in GROQ_AVAILABLE_MODELS
            )
            embed = _base_embed(
                f"{ICO_AI}  Active Groq Model",
                f"**Current:** `{self.groq_model}`\n\n**Available:**\n{lines}\n\n👁️ = supports image input",
                COL_AI,
            )
            embed.set_footer(text="Bot owners can switch: !aimodel <name>")
            return await ctx.send(embed=embed)

        if ctx.author.id not in OWNERS:
            return await ctx.send(embed=_err("Only bot owners can change the model."), ephemeral=True)

        model = model.strip().lower()
        if model not in GROQ_AVAILABLE_MODELS:
            return await ctx.send(embed=_err(f"`{model}` is not a recognised Groq model.\nRun `!aimodel` to see the list."), ephemeral=True)

        old, self.groq_model = self.groq_model, model
        embed = _base_embed(f"{ICO_OK}  Groq Model Changed", f"**From:** `{old}`\n**To:** `{self.groq_model}`", COL_SUCCESS)
        embed.set_footer(text=f"Changed by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !aisystem ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="aisystem", description="View or update the Groq AI system prompt.")
    @commands.guild_only()
    @app_commands.describe(prompt="New system prompt — leave blank to view the current one")
    async def aisystem(self, ctx: commands.Context, *, prompt: str = None):
        """View or update the Groq AI's system prompt. Only bot owners can change this."""
        if prompt is None:
            embed = _base_embed(f"{ICO_AI}  Groq System Prompt", f"```{self.groq_system_prompt[:1900]}```", COL_AI)
            embed.set_footer(text="Bot owners can update: !aisystem <new prompt>")
            return await ctx.send(embed=embed, ephemeral=True)

        if ctx.author.id not in OWNERS:
            return await ctx.send(embed=_err("Only bot owners can change the system prompt."), ephemeral=True)

        prompt = prompt.strip()
        if len(prompt) > 2000:
            return await ctx.send(embed=_err("System prompt must be 2000 characters or fewer."), ephemeral=True)

        self.groq_system_prompt = prompt
        embed = _base_embed(f"{ICO_OK}  Groq System Prompt Updated", f"```{self.groq_system_prompt[:1500]}```", COL_SUCCESS)
        embed.set_footer(text=f"Changed by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !aimemory ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="aimemory", description="View or set how many messages Groq AI remembers per channel.")
    @commands.guild_only()
    @app_commands.describe(value="Number of exchange pairs to remember (1-50)")
    async def aimemory(self, ctx: commands.Context, value: int = None):
        """View or change how many message pairs !chat remembers. Owners only. Range: 1-50."""
        if value is None:
            embed = _base_embed(
                f"{ICO_AI}  Groq Chat Memory",
                f"Currently remembering the last **{self.groq_max_history}** exchange pairs per channel.\n*(Default: {DEFAULT_MAX_HISTORY})*",
                COL_AI,
            )
            embed.set_footer(text="Bot owners can change: !aimemory <number>")
            return await ctx.send(embed=embed)

        if ctx.author.id not in OWNERS:
            return await ctx.send(embed=_err("Only bot owners can change this."), ephemeral=True)
        if not (1 <= value <= 50):
            return await ctx.send(embed=_err("Value must be between **1** and **50**."), ephemeral=True)

        old, self.groq_max_history = self.groq_max_history, value
        embed = _base_embed(f"{ICO_OK}  Groq Memory Updated", f"**From:** `{old}` pairs\n**To:** `{self.groq_max_history}` pairs", COL_SUCCESS)
        embed.set_footer(text=f"Changed by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !aistats ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="aistats", description="Show Groq AI session usage statistics.")
    @commands.guild_only()
    async def aistats(self, ctx: commands.Context):
        """Show Groq request counts, token usage, and error stats for this session."""
        embed = self._stats_embed(
            self._groq_stats, self.groq_model, self._groq_history,
            self.groq_max_history, ICO_AI, COL_AI, "Groq AI"
        )
        await ctx.send(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  OPENROUTER COMMANDS  (prefix: c)
    # ══════════════════════════════════════════════════════════════════════════

    # ── !cask ─────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="cask", description="Ask OpenRouter AI a one-shot question. Attach images or files!")
    @commands.guild_only()
    @commands.cooldown(1, 5.0, commands.BucketType.user)
    @app_commands.describe(question="What do you want to ask?")
    async def cask(self, ctx: commands.Context, *, question: str = ""):
        """Ask the OpenRouter AI a single question — no memory.
Attach an image or text file to include it as context.
Supports slash: /cask"""
        question = question.strip()
        if not question and not ctx.message.attachments:
            return await ctx.send(embed=_err("Please include a question or attachment."), ephemeral=True)

        session = await self._get_session()
        supports_vision = self.or_model in OR_VISION_MODELS
        content, notices = await _process_attachments(
            ctx.message.attachments, question, session, supports_vision=supports_vision
        )

        msg = await self._thinking(ctx, ICO_OR, COL_OR)
        try:
            answer, usage = await self._call_openrouter([{"role": "user", "content": content}])
            await self._edit_reply(
                msg, ctx.author, question or "(see attachment)", answer, usage,
                subtitle="Ask", model=self.or_model, icon=ICO_OR, colour=COL_OR,
                notices=notices or None,
            )
        except RuntimeError as e:
            await msg.edit(embed=_err(str(e)))
        except Exception as e:
            log.exception("Unexpected error in !cask")
            await msg.edit(embed=_err(f"Unexpected error: {e}"))

    # ── !cchat ────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="cchat", description="Chat with OpenRouter AI — remembers recent messages. Attach images or files!")
    @commands.guild_only()
    @commands.cooldown(1, 5.0, commands.BucketType.user)
    @app_commands.describe(message="Your message to the AI")
    async def cchat(self, ctx: commands.Context, *, message: str = ""):
        """Chat with OpenRouter AI. Remembers the last N exchanges in this channel.
Attach an image or text file to include it as context.
Use !cclear to wipe history. Supports slash: /cchat"""
        message = message.strip()
        if not message and not ctx.message.attachments:
            return await ctx.send(embed=_err("Please include a message or attachment."), ephemeral=True)

        session = await self._get_session()
        supports_vision = self.or_model in OR_VISION_MODELS
        content, notices = await _process_attachments(
            ctx.message.attachments, message, session, supports_vision=supports_vision
        )

        history = self._get_history(self._or_history, ctx.channel.id)
        history.append({"role": "user", "content": content})

        msg = await self._thinking(ctx, ICO_OR, COL_OR)
        try:
            answer, usage = await self._call_openrouter(history)
            self._push_history(self._or_history, self.or_max_history, ctx.channel.id, "user", content)
            self._push_history(self._or_history, self.or_max_history, ctx.channel.id, "assistant", answer)
            pairs = len(self._or_history[ctx.channel.id]) // 2
            await self._edit_reply(
                msg, ctx.author, message or "(see attachment)", answer, usage,
                subtitle=f"Chat  (memory: {pairs}/{self.or_max_history})",
                model=self.or_model, icon=ICO_OR, colour=COL_OR,
                notices=notices or None,
            )
        except RuntimeError as e:
            await msg.edit(embed=_err(str(e)))
        except Exception as e:
            log.exception("Unexpected error in !cchat")
            await msg.edit(embed=_err(f"Unexpected error: {e}"))

    # ── !cclear ───────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="cclear", description="Clear the OpenRouter AI conversation history for this channel.")
    @commands.guild_only()
    async def cclear(self, ctx: commands.Context):
        """Wipe the !cchat memory for this channel."""
        count = len(self._or_history[ctx.channel.id])
        self._clear_history(self._or_history, ctx.channel.id)
        embed = _base_embed(
            f"{ICO_OK}  OpenRouter Conversation Cleared",
            f"Removed **{count}** message{'s' if count != 1 else ''} from this channel's OpenRouter memory.",
            COL_SUCCESS,
        )
        embed.set_footer(text=f"Cleared by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !caimodel ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="caimodel", description="Show or switch the active OpenRouter model.")
    @commands.guild_only()
    @app_commands.describe(model="Model to switch to (leave blank to see options)")
    async def caimodel(self, ctx: commands.Context, *, model: str = None):
        """View available OpenRouter models or switch the active one. Switching requires bot owner."""
        if model is None:
            lines = "\n".join(
                f"{'▶ ' if m == self.or_model else '  '}`{m}`"
                + (" 👁️" if m in OR_VISION_MODELS else "")
                for m in OR_AVAILABLE_MODELS
            )
            embed = _base_embed(
                f"{ICO_OR}  Active OpenRouter Model",
                f"**Current:** `{self.or_model}`\n\n**Available:**\n{lines}\n\n👁️ = supports image input",
                COL_OR,
            )
            embed.set_footer(text="Bot owners can switch: !caimodel <name>")
            return await ctx.send(embed=embed)

        if ctx.author.id not in OWNERS:
            return await ctx.send(embed=_err("Only bot owners can change the model."), ephemeral=True)

        model = model.strip()
        if model not in OR_AVAILABLE_MODELS:
            return await ctx.send(embed=_err(f"`{model}` is not in the model list.\nRun `!caimodel` to see options."), ephemeral=True)

        old, self.or_model = self.or_model, model
        embed = _base_embed(f"{ICO_OK}  OpenRouter Model Changed", f"**From:** `{old}`\n**To:** `{self.or_model}`", COL_SUCCESS)
        embed.set_footer(text=f"Changed by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !caisystem ────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="caisystem", description="View or update the OpenRouter AI system prompt.")
    @commands.guild_only()
    @app_commands.describe(prompt="New system prompt — leave blank to view the current one")
    async def caisystem(self, ctx: commands.Context, *, prompt: str = None):
        """View or update the OpenRouter AI's system prompt. Only bot owners can change this."""
        if prompt is None:
            embed = _base_embed(f"{ICO_OR}  OpenRouter System Prompt", f"```{self.or_system_prompt[:1900]}```", COL_OR)
            embed.set_footer(text="Bot owners can update: !caisystem <new prompt>")
            return await ctx.send(embed=embed, ephemeral=True)

        if ctx.author.id not in OWNERS:
            return await ctx.send(embed=_err("Only bot owners can change the system prompt."), ephemeral=True)

        prompt = prompt.strip()
        if len(prompt) > 2000:
            return await ctx.send(embed=_err("System prompt must be 2000 characters or fewer."), ephemeral=True)

        self.or_system_prompt = prompt
        embed = _base_embed(f"{ICO_OK}  OpenRouter System Prompt Updated", f"```{self.or_system_prompt[:1500]}```", COL_SUCCESS)
        embed.set_footer(text=f"Changed by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !caimemory ────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="caimemory", description="View or set how many messages OpenRouter AI remembers per channel.")
    @commands.guild_only()
    @app_commands.describe(value="Number of exchange pairs to remember (1-50)")
    async def caimemory(self, ctx: commands.Context, value: int = None):
        """View or change how many message pairs !cchat remembers. Owners only. Range: 1-50."""
        if value is None:
            embed = _base_embed(
                f"{ICO_OR}  OpenRouter Chat Memory",
                f"Currently remembering the last **{self.or_max_history}** exchange pairs per channel.\n*(Default: {DEFAULT_MAX_HISTORY})*",
                COL_OR,
            )
            embed.set_footer(text="Bot owners can change: !caimemory <number>")
            return await ctx.send(embed=embed)

        if ctx.author.id not in OWNERS:
            return await ctx.send(embed=_err("Only bot owners can change this."), ephemeral=True)
        if not (1 <= value <= 50):
            return await ctx.send(embed=_err("Value must be between **1** and **50**."), ephemeral=True)

        old, self.or_max_history = self.or_max_history, value
        embed = _base_embed(f"{ICO_OK}  OpenRouter Memory Updated", f"**From:** `{old}` pairs\n**To:** `{self.or_max_history}` pairs", COL_SUCCESS)
        embed.set_footer(text=f"Changed by {ctx.author}")
        await ctx.send(embed=embed)

    # ── !caistats ─────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="caistats", description="Show OpenRouter AI session usage statistics.")
    @commands.guild_only()
    async def caistats(self, ctx: commands.Context):
        """Show OpenRouter request counts, token usage, and error stats for this session."""
        embed = self._stats_embed(
            self._or_stats, self.or_model, self._or_history,
            self.or_max_history, ICO_OR, COL_OR, "OpenRouter AI"
        )
        await ctx.send(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  HELP EMBED
    # ══════════════════════════════════════════════════════════════════════════

    def help_embed(self, prefix: str = "!") -> discord.Embed:
        embed = discord.Embed(
            title=f"{ICO_AI}  AI Chat — Groq + OpenRouter",
            colour=COL_AI,
            timestamp=_now(),
        )
        embed.description = (
            f"Two AI backends available — **Groq** (fast, free) and **OpenRouter** (GPT-4o, Claude, Gemini, …).\n"
            f"Both support **image** and **text file** attachments.\n"
            f"*Only bot owners can change models or system prompts.*"
        )

        # Groq commands
        embed.add_field(name="── Groq AI ──────────────────────", value="\u200b", inline=False)
        embed.add_field(name=f"`{prefix}ask <question>`",    value="One-shot Q&A. Attach image or file for context.",     inline=False)
        embed.add_field(name=f"`{prefix}chat <message>`",    value=f"Conversational — remembers last {self.groq_max_history} exchanges. Attach image or file.", inline=False)
        embed.add_field(name=f"`{prefix}aiclear`",           value="Wipe this channel's Groq AI memory.",                 inline=False)
        embed.add_field(name=f"`{prefix}aimodel [model]`",   value="Show/switch Groq model (owners only). 👁️ = vision.",  inline=False)
        embed.add_field(name=f"`{prefix}aisystem [prompt]`", value="View/update Groq system prompt (owners only).",       inline=False)
        embed.add_field(name=f"`{prefix}aimemory [n]`",      value="View/set Groq memory depth (owners only).",           inline=False)
        embed.add_field(name=f"`{prefix}aistats`",           value="Groq session stats.",                                 inline=False)

        # OpenRouter commands
        embed.add_field(name="── OpenRouter AI ────────────────", value="\u200b", inline=False)
        embed.add_field(name=f"`{prefix}cask <question>`",    value="One-shot Q&A via OpenRouter. Attach image or file.", inline=False)
        embed.add_field(name=f"`{prefix}cchat <message>`",    value=f"Conversational via OpenRouter — remembers last {self.or_max_history} exchanges.", inline=False)
        embed.add_field(name=f"`{prefix}cclear`",             value="Wipe this channel's OpenRouter memory.",             inline=False)
        embed.add_field(name=f"`{prefix}caimodel [model]`",   value="Show/switch OpenRouter model (owners only). 👁️ = vision.", inline=False)
        embed.add_field(name=f"`{prefix}caisystem [prompt]`", value="View/update OpenRouter system prompt (owners only).", inline=False)
        embed.add_field(name=f"`{prefix}caimemory [n]`",      value="View/set OpenRouter memory depth (owners only).",    inline=False)
        embed.add_field(name=f"`{prefix}caistats`",           value="OpenRouter session stats.",                          inline=False)

        embed.set_footer(text=f"{ICO_AI} AI  |  Use {prefix}cmd or /cmd  |  14 commands")
        return embed


async def setup(bot):
    await bot.add_cog(AI(bot))
