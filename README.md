# MonkeyBytes Moderation

A self-hostable Discord bot written in Python 3.11+ and discord.py 2.x. Includes moderation, antinuke, ticketing, AI chat (Groq and OpenRouter), an economy system, utility tools, and more — all as hot-reloadable cogs.

Both prefix (`!cmd`) and slash (`/cmd`) commands are supported for nearly every feature.

---

## Features

- **Paladin** — Antinuke and AutoMod: banned words, anti-invite, mass-mention detection, raid spam, role/channel wipe prevention, ban/kick flood limits
- **Reversion** — Automatically reverses bad actor actions: re-creates wiped channels and roles, unbans victims, removes unauthorized bots and webhooks
- **Tickets** — Dropdown ticket panel with claim/close buttons and HTML transcripts
- **AI Chat** — Groq (free, fast) and OpenRouter (GPT-4o, Claude, Gemini, and more) backends with vision and file context support
- **Blackjack** — 6-deck shoe, double-down, daily chips, global leaderboard
- **Button Roles** — Persistent, restart-safe self-service role buttons
- **Encryption** — Encode/decode base16, base32, base64, base85, ASCII85, ROT13, hex
- **Logging** — Per-guild event and mod-action logging
- **Message Stats** — First message lookup and per-guild leaderboard
- **Extras** — Snipe, polls, reminders, giveaways, AFK, trivia, tic-tac-toe, and more
- **Fun** — 8-ball, dice, slots, coinflip, roulette, and more
- **Info & Server Tools** — ping, about, server info, user info, avatar lookup

---

## Getting Started

### Requirements

- Python 3.11+
- A Discord application and bot token — [Discord Developer Portal](https://discord.com/developers/applications)
- *(Optional)* A Groq API key — [console.groq.com](https://console.groq.com)
- *(Optional)* An OpenRouter API key — [openrouter.ai](https://openrouter.ai)

### Installation

```bash
git clone https://github.com/AakarshIs0P/MonkeyBytesModeration.git
cd MonkeyBytesModeration
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your-bot-token-here
DISCORD_OWNER_IDS=123456789012345678,987654321098765432

# Optional — only required for AI commands
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxx
```

### Gateway Intents

In the Discord Developer Portal under the **Bot** tab, enable:

- Presence Intent
- Server Members Intent
- Message Content Intent

### Running

```bash
python index.py
```

Expected output:

```
[INFO] bot: Initialising bot...
[INFO] bot.data: Loaded cog: cogs.msg_stats
...
[INFO] bot.data: Slash commands synced: 60+ commands
[INFO] bot: Logged in as MonkeyBytes Moderation (ID: ...)
```

---

## Project Layout

```
index.py        — entry point
cogs/           — all features as hot-reloadable cogs (auto-loaded alphabetically)
utils/          — config, data layer, helpers, permissions
data/           — runtime JSON state (auto-created)
.env            — secrets (not committed)
```

Cogs are hot-reloadable with `!reload <cog>` (owner only) — no restart needed while iterating.

---

## Command Reference

The default prefix is `!`. Every command also has a `/slash` equivalent unless noted. Commands marked 🔒 are owner-only.

### Fun (`cogs/fun.py`)

| Command | Description |
|---|---|
| `8ball <question>` | Magic 8-ball |
| `duck` / `cat` / `dog` | Random animal image |
| `coffee` / `beer` | Send a virtual drink |
| `coinflip` | Heads or tails |
| `slot` | Slot machine |
| `dice [sides]` | Roll a die |
| `roulette` | Russian roulette |
| `rate <thing>` | Rate something 0–10 |
| `reverse <text>` | Reverse text |
| `password [length]` | Generate a strong password |
| `hotcalc <a> <b>` | Compatibility calculator |
| `randomfact` | A random fact |

### Extras (`cogs/extras.py`)

| Command | Description |
|---|---|
| `snipe` | Show the last deleted message in this channel |
| `poll <option1> \| <option2> ...` | Create a poll |
| `remindme <duration> <message>` | Set a personal reminder |
| `reminders` | List active reminders |
| `tictactoe @user` | Play tic-tac-toe |
| `trivia` | Random trivia question |
| `giveaway <duration> <prize>` | Start a giveaway (requires Manage Messages) |
| `choose <a, b, c>` | Pick one option at random |
| `afk [reason]` | Set AFK status (clears on next message) |

### Information (`cogs/info.py`)

| Command | Description |
|---|---|
| `ping` | Bot and API latency |
| `invite` | Bot invite link |
| `about` | Bot stats |
| `covid <country>` | COVID-19 statistics |

### Server & Users (`cogs/discord_info.py`)

| Command | Description |
|---|---|
| `avatar [user]` | Show a user's avatar |
| `roles` | List server roles |
| `joinedat [user]` | When a member joined |
| `mods` | Online staff list |
| `server` | Server info |
| `user [user]` | User info |

### Message Stats (`cogs/msg_stats.py`)

| Command | Description |
|---|---|
| `firstmessage [user]` | Find a user's first message in this server |
| `msgleaderboard` | Top 10 chatters in this server |

State is stored in `data/msg_counts.json`, flushed to disk every 2 minutes.

### Blackjack (`cogs/blackjack.py`)

| Command | Description |
|---|---|
| `blackjack [bet]` | Play a hand (Hit / Stand / Double) |
| `bjbalance [user]` | Check chip balance |
| `bjdaily` | Claim 500 free chips (resets daily, UTC) |
| `bjleaderboard` | Global chip leaderboard |

State is stored in `data/bj_chips.json` and `data/bj_daily.json`. Starting balance: 1,000 chips.

### Moderation (`cogs/mod.py`)

`kick`, `ban`, `unban`, `mute`, `unmute`, `timeout`, `untimeout`, `masskick`, `massban`, `nickname`, `purge`, `slowmode`, `lock`, `unlock`, `hide`, `unhide`, `find`, `announcerole`, `role`

### Warnings (`cogs/warns.py`)

`warn @user <reason>`, `warnings [user]`, `clearwarn @user [index|all]`

State: `data/warns.json`

### Logging (`cogs/logging_cog.py`)

| Command | Description |
|---|---|
| `setlog #channel` | Enable logging to a channel |
| `unsetlog` | Disable logging |

Logs joins, leaves, message edits and deletes, role changes, channel changes, and more.

### Encryption (`cogs/encryption.py`)

`encode <type> <text>` / `decode <type> <text>`

Supported types: `base16`, `base32`, `base64`, `base85`, `ascii85`, `hex`, `rot13`

### Button Roles (`cogs/buttonroles.py`)

| Command | Description |
|---|---|
| `buttonroles #channel <emoji>:@role ...` | Send a persistent role-picker message |

Buttons are re-registered on startup and survive restarts.

### AI Chat (`cogs/ai.py`) 🔒

**Groq backend** (free, fast):

| Command | Description |
|---|---|
| `ask <question>` | One-shot Q&A. Supports image and text file attachments. |
| `chat <message>` | Conversational; remembers the last N exchanges per channel |
| `aiclear` | Wipe this channel's conversation memory |
| `aimodel [model]` | View or switch the Groq model 🔒 |
| `aisystem [prompt]` | View or set the system prompt 🔒 |
| `aimemory [n]` | View or set memory depth 🔒 |
| `aistats` | Session usage stats |

**OpenRouter backend** (paid — GPT-4o, Claude, Gemini, and more):

| Command | Description |
|---|---|
| `cask <question>` | One-shot Q&A via OpenRouter |
| `cchat <message>` | Conversational via OpenRouter |
| `cclear` | Wipe OpenRouter memory |
| `caimodel [model]` | View or switch the OpenRouter model 🔒 |
| `caisystem [prompt]` | System prompt 🔒 |
| `caimemory [n]` | Memory depth 🔒 |
| `caistats` | Session usage stats |

Both backends accept image attachments (PNG/JPEG/GIF/WEBP) on vision-capable models, and any plain-text file (`.py`, `.txt`, `.json`, `.md`, `.log`, etc.) up to 32 KB as additional context.

### Paladin — Antinuke & AutoMod (`cogs/paladin.py`) 🔒 server owner only

Paladin monitors actions per-actor within a sliding time window. When a threshold is crossed, it strips the actor's roles and triggers Reversion.

**Antinuke:**

```
paladin enable | disable
paladin status
paladin set <event> <count> <window_seconds>
paladin reset <event>
paladin actions
```

Events: `ban`, `kick`, `channel_create`, `channel_delete`, `role_create`, `role_delete`, `guild_update`, `bot_add`, `webhook`

**AutoMod:**

```
automod enable | disable
automod status
automod bannedwords add | remove | list <word>
automod antiinvite on | off
automod mentions <limit>
automod spam <messages> <seconds>
automod punish <warn|mute|kick|ban> <strikes_required> [timeout_minutes]
automod warnexpire <seconds>
```

**Whitelist & Alerts:**

```
whitelist @user
whitelistshow
alertchannel #channel
```

### Reversion (`cogs/reversion.py`)

No commands. Runs automatically when Paladin trips a threshold — re-creates deleted channels and roles, unbans victims, DMs kicked members an invite, restores guild settings, and removes unauthorized bots and webhooks.

### Tickets (`cogs/tickets.py`)

| Command | Description |
|---|---|
| `ticket setup #panel @support [#log]` | Deploy a dropdown ticket panel |
| `ticket add @user` | Add a user to the current ticket |
| `ticket remove @user` | Remove a user from the current ticket |
| `ticket close` | Close the ticket and generate a transcript |

Tickets include Claim and Close buttons. Transcripts are exported to the configured log channel via `chat_exporter`.

### Admin (`cogs/admin.py`) 🔒

| Command | Description |
|---|---|
| `load <cog>` | Load a cog |
| `unload <cog>` | Unload a cog |
| `reload <cog>` | Reload a cog |
| `reloadall` | Reload all cogs |
| `reloadutils <name>` | Reload a `utils/*.py` module |
| `dm <user_id> <message>` | DM a user as the bot |
| `announce #channel <message>` | Send an announcement |
| `change username <name>` | Change the bot's username |
| `change avatar <url>` | Change the bot's avatar |

---

## Data & Persistence

All runtime state lives in plain JSON under `data/`:

| File | Cog | Contents |
|---|---|---|
| `bj_chips.json` | Blackjack | `{ user_id: chips }` |
| `bj_daily.json` | Blackjack | `{ user_id: "YYYY-MM-DD" }` |
| `msg_counts.json` | MsgStats | `{ guild_id: { user_id: count } }` |
| `warns.json` | Warns | `{ guild_id: { user_id: [warns…] } }` |
| `tickets.json` | Tickets | Per-guild config and open ticket state |
| `buttonroles.json` | ButtonRoles | Message ↔ role mappings |
| `log_channels.json` | Logging | `{ guild_id: channel_id }` |
| `paladin.json`, `paladin_automod.json`, etc. | Paladin | Per-guild config and event state |
| `giveaways.json`, `reminders.json` | Extras | Scheduled state |

Caches flush on a timer and on graceful shutdown. Every JSON write is atomic (`tmp` file + `os.replace`).

---

## Hot Reloading

```bash
!reload extras            # reload one cog
!reloadall                # reload all cogs
!reloadutils permissions  # reload a utils module
```

You can iterate on any cog without dropping the gateway connection.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Slash commands don't appear | Wait up to 1 hour for global sync, or re-invite the bot with the `applications.commands` scope |
| `LoginFailure` | Token is invalid or has been regenerated |
| AI commands return "Network error" | Check that `GROQ_API_KEY` or `OPENROUTER_API_KEY` is set correctly in `.env` |
| Paladin doesn't fire | Run `paladin enable`; verify the actor isn't whitelisted or a server owner |
| Tickets don't open | Run `ticket setup` first |
| `MissingPermissions` on mod commands | Move the bot's role above the target role in the server's role list |

---

## Contributing

PRs are welcome. A few conventions to follow:

- Add new features as a cog under `cogs/`
- Keep all I/O async-safe — use `asyncio.Lock` for shared mutable state
- Implement `help_embed(prefix)` or fall back to docstrings for help text
- Persist state under `data/` using atomic `tmp` + `os.replace` writes

---

## License

MIT — use it however you like.
