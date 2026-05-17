import base64
import binascii
import codecs
import discord

from io import BytesIO
from discord.ext import commands
from utils.default import CustomContext
from utils import default, http
from utils.data import DiscordBot


async def encryptout(ctx_or_interaction, convert: str, input_data) -> None:
    """Send encode/decode result — works for both prefix and slash."""
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    send = ctx_or_interaction.followup.send if is_interaction else ctx_or_interaction.send

    if not input_data:
        msg = "❌ You need to provide something to encode/decode."
        if is_interaction:
            return await ctx_or_interaction.followup.send(msg, ephemeral=True)
        return await ctx_or_interaction.send(msg)

    try:
        text = input_data.decode("utf-8")
    except AttributeError:
        text = input_data

    if len(text) > 1900:
        data = BytesIO(text.encode("utf-8") if isinstance(text, str) else text)
        try:
            return await send(content=f"📑 **{convert}**", file=discord.File(data, filename=default.timetext("Encryption")))
        except discord.HTTPException:
            return await send("❌ The output file exceeded 8 MB, sorry.")
    await send(f"📑 **{convert}**```fix\n{text}```")


class Encryption(commands.Cog):
    def __init__(self, bot):
        self.bot: DiscordBot = bot

    # ── Prefix groups ─────────────────────────────────────────────────

    @commands.group()
    async def encode(self, ctx: CustomContext):
        """ Encode text using various methods. """
        if ctx.invoked_subcommand is None:
            await ctx.send_help(str(ctx.command))

    @commands.group()
    async def decode(self, ctx: CustomContext):
        """ Decode text using various methods. """
        if ctx.invoked_subcommand is None:
            await ctx.send_help(str(ctx.command))

    # ── Base32 ────────────────────────────────────────────────────────

    @encode.command(name="base32", aliases=["b32"])
    async def encode_base32(self, ctx, *, input: commands.clean_content = None):
        """ Encode text to base32. """
        await encryptout(ctx, "Text → base32", base64.b32encode((input or "").encode("utf-8")))

    @decode.command(name="base32", aliases=["b32"])
    async def decode_base32(self, ctx, *, input: commands.clean_content = None):
        """ Decode base32 to text. """
        try:
            await encryptout(ctx, "base32 → Text", base64.b32decode((input or "").encode("utf-8")))
        except Exception:
            await ctx.send("❌ Invalid base32 input.")

    @encode.command(name="base64", aliases=["b64"])
    async def encode_base64(self, ctx, *, input: commands.clean_content = None):
        """ Encode text to base64. """
        await encryptout(ctx, "Text → base64", base64.urlsafe_b64encode((input or "").encode("utf-8")))

    @decode.command(name="base64", aliases=["b64"])
    async def decode_base64(self, ctx, *, input: commands.clean_content = None):
        """ Decode base64 to text. """
        try:
            await encryptout(ctx, "base64 → Text", base64.urlsafe_b64decode((input or "").encode("utf-8")))
        except Exception:
            await ctx.send("❌ Invalid base64 input.")

    @encode.command(name="rot13", aliases=["r13"])
    async def encode_rot13(self, ctx, *, input: commands.clean_content = None):
        """ Encode text with ROT13. """
        await encryptout(ctx, "Text → ROT13", codecs.decode(input or "", "rot_13"))

    @decode.command(name="rot13", aliases=["r13"])
    async def decode_rot13(self, ctx, *, input: commands.clean_content = None):
        """ Decode ROT13 to text. """
        await encryptout(ctx, "ROT13 → Text", codecs.decode(input or "", "rot_13"))

    @encode.command(name="hex")
    async def encode_hex(self, ctx, *, input: commands.clean_content = None):
        """ Encode text to hex. """
        await encryptout(ctx, "Text → Hex", binascii.hexlify((input or "").encode("utf-8")))

    @decode.command(name="hex")
    async def decode_hex(self, ctx, *, input: commands.clean_content = None):
        """ Decode hex to text. """
        try:
            await encryptout(ctx, "Hex → Text", binascii.unhexlify((input or "").encode("utf-8")))
        except Exception:
            await ctx.send("❌ Invalid hex input.")

    @encode.command(name="base85", aliases=["b85"])
    async def encode_base85(self, ctx, *, input: commands.clean_content = None):
        """ Encode text to base85. """
        await encryptout(ctx, "Text → base85", base64.b85encode((input or "").encode("utf-8")))

    @decode.command(name="base85", aliases=["b85"])
    async def decode_base85(self, ctx, *, input: commands.clean_content = None):
        """ Decode base85 to text. """
        try:
            await encryptout(ctx, "base85 → Text", base64.b85decode((input or "").encode("utf-8")))
        except Exception:
            await ctx.send("❌ Invalid base85 input.")

    @encode.command(name="ascii85", aliases=["a85"])
    async def encode_ascii85(self, ctx, *, input: commands.clean_content = None):
        """ Encode text to ASCII85. """
        await encryptout(ctx, "Text → ASCII85", base64.a85encode((input or "").encode("utf-8")))

    @decode.command(name="ascii85", aliases=["a85"])
    async def decode_ascii85(self, ctx, *, input: commands.clean_content = None):
        """ Decode ASCII85 to text. """
        try:
            await encryptout(ctx, "ASCII85 → Text", base64.a85decode((input or "").encode("utf-8")))
        except Exception:
            await ctx.send("❌ Invalid ASCII85 input.")


async def setup(bot):
    await bot.add_cog(Encryption(bot))
