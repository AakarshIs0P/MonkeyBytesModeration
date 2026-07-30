import time
import json
import discord
import traceback

from discord.ext import commands
from typing import TYPE_CHECKING
from datetime import datetime, timezone
from io import BytesIO

if TYPE_CHECKING:
    from utils.data import DiscordBot


class CustomContext(commands.Context):
    def __init__(self, **kwargs):
        self.bot: "DiscordBot"
        super().__init__(**kwargs)


def load_json(filename: str = "config.json") -> dict:
    try:
        with open(filename, encoding='utf8') as data:
            return json.load(data)
    except FileNotFoundError:
        raise FileNotFoundError("JSON file wasn't found")


def traceback_maker(err, advance: bool = True) -> str:
    _traceback = "".join(traceback.format_tb(err.__traceback__))
    error = f"```py\n{_traceback}{type(err).__name__}: {err}\n```"
    return error if advance else f"{type(err).__name__}: {err}"


def timetext(name) -> str:
    return f"{name}_{int(time.time())}.txt"


def date(
    target, clock: bool = True,
    ago: bool = False, only_ago: bool = False
) -> str:
    if isinstance(target, int) or isinstance(target, float):
        target = datetime.fromtimestamp(target, tz=timezone.utc)

    unix = int(time.mktime(target.timetuple()))
    timestamp = f"<t:{unix}:{'f' if clock else 'D'}>"
    if ago:
        timestamp += f" (<t:{unix}:R>)"
    if only_ago:
        timestamp = f"<t:{unix}:R>"
    return timestamp


def responsible(target: discord.Member, reason: str) -> str:
    responsible = f"[ {target} ]"
    if not reason:
        return f"{responsible} no reason given..."
    return f"{responsible} {reason}"


def actionmessage(case: str, mass: bool = False) -> str:
    output = f"**{case}** the user"
    if mass:
        output = f"**{case}** the IDs/Users"
    return f"✅ Successfully {output}"


async def pretty_results(
    ctx: CustomContext, filename: str = "Results",
    resultmsg: str = "Here's the results:", loop: list = None
) -> None:
    if not loop:
        return await ctx.send("The result was empty...")

    pretty = "\r\n".join([f"[{str(num).zfill(2)}] {data}" for num, data in enumerate(loop, start=1)])

    if len(loop) < 15:
        return await ctx.send(f"{resultmsg}```ini\n{pretty}```")

    data = BytesIO(pretty.encode('utf-8'))
    await ctx.send(
        content=resultmsg,
        file=discord.File(
            data,
            filename=timetext(filename.title())
        )
    )
