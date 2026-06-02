# 🐟 KoiLa — A Powerful, Modular Discord Bot

KoiLa is a feature-rich, self-hostable Discord bot written in **Python 3.11+** and
**[discord.py 2.x](https://discordpy.readthedocs.io/)**.
It bundles moderation, antinuke, tickets, two AI backends (Groq + OpenRouter),
blackjack with chips, message stats, button roles, encryption utilities, fun
games, and more — all as hot-reloadable cogs.

> Both **prefix** (`!cmd`) and **slash** (`/cmd`) commands are supported for almost
> every feature.

---

## ✨ Highlights

- 🛡️ **Paladin** — Antinuke + AutoMod (banned words, anti-invite, mass-mention, raid spam, role/channel wipes, ban/kick floods…)
- ⚡ **Reversion** — Automatically *reverses* bad actions (re-creates wiped channels/roles, unbans victims, kicks rogue bots)
- 🎟️ **Tickets** — Dropdown ticket panel with claim/close buttons + **HTML transcripts**
- 🤖 **AI Chat** — Two backends: **Groq** (free + fast) and **OpenRouter** (GPT-4o, Claude 3.5, Gemini, …) with **vision** + **file context**
- 🃏 **Blackjack** — 6-deck shoe, double-down, daily chips, **global leaderboard**
- 🎭 **Button Roles** — Persistent, restart-safe self-service role buttons
- 🔐 **Encryption** — Encode/decode base16/32/64/85, ASCII85, ROT13, hex
- 📋 **Logging** — Per-guild event + mod-action logging
- 💬 **Message Stats** — `firstmessage` lookup + per-guild leaderboard
- ⭐ **Extras** — Snipe, polls, reminders, giveaways, AFK, trivia, tic-tac-toe
- 🎮 **Fun** — 8ball, dice, slot, coinflip, roulette, hot calc, randomfact, …
- 📊 **Information & Server tools** — `ping`, `about`, `server`, `user`, `avatar`, …

---

## 🚀 Quick Start

### 1. Requirements

- Python **3.11+**
- A Discord application + bot token → <https://discord.com/developers/applications>
- (Optional) A Groq API key → <https://console.groq.com>
- (Optional) An OpenRouter API key → <https://openrouter.ai>

### 2. Install

```bash
git clone https://github.com/AakarshIs0P/MonkeyBytesModeration.git koila
cd koila
pip install -r requirements.txt
```

### 3. Configure

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your-bot-token-here
DISCORD_OWNER_IDS=123456789012345678,987654321098765432

# Optional — only needed for AI cog
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxx
```

### 4. Enable required gateway intents

In the Discord Developer Portal **Bot** tab, enable:

- ✅ Presence Intent
- ✅ Server Members Intent
- ✅ Message Content Intent

### 5. Run

```bash
python index.py
```

You should see:

```
[INFO] bot: Initialising bot...
[INFO] bot.data: Loaded cog: cogs.msg_stats
... (other cogs)
[INFO] bot.data: Slash commands synced: 60+ commands
[INFO] bot: Logged in as KoiLa (ID: …)
```

---

## 🗂️ Project Layout

```
index.py            ← single entry point
cogs/               ← every feature is a cog (auto-loaded alphabetically)
utils/              ← config, data layer, helpers, permissions
data/               ← runtime JSON state (auto-created)
.env                ← secrets (not committed)
```

Cogs are **hot-reloadable** with `!reload <cog>` (owner only) — no bot restart
required while you iterate.

---

## 📚 Cog & Command Reference

> Default prefix is `!`. Every command also has a `/slash` equivalent unless
> noted. Owner-only commands are marked 🔒.

### 🎮 Fun (`cogs/fun.py`)
| Command | Description |
|---|---|
| `8ball <question>` | Magic 8-ball |
| `duck` / `cat` / `dog` | Random animal pic |
| `coffee` / `beer` | Send a virtual drink |
| `coinflip` | Heads or tails |
| `slot` | 🎰 Slot machine |
| `dice [sides]` | Roll a die |
| `roulette` | Russian roulette |
| `rate <thing>` | 0–10 rating |
| `reverse <text>` | Reverse text |
| `password [length]` | Generate a strong password |
| `hotcalc <a> <b>` | Compatibility calculator |
| `randomfact` | A random fact |

### ⭐ Extras (`cogs/extras.py`)
| Command | Description |
|---|---|
| `snipe` | Show last deleted message in this channel |
| `poll <question> | option1 | option2 …` | Reaction poll |
| `remindme <duration> <message>` | Personal reminder |
| `reminders` | List your active reminders |
| `tictactoe @user` | Play TTT |
| `trivia` | Random trivia question |
| `giveaway <duration> <prize>` | Start a giveaway (manage_messages) |
| `choose a, b, c` | Pick one at random |
| `afk [reason]` | Set AFK status (auto-clears on next message) |

### 📊 Information (`cogs/info.py`)
| Command | Description |
|---|---|
| `ping` | Bot + API latency |
| `invite` | Bot invite link |
| `about` | Bot stats |
| `covid <country>` | COVID-19 stats |

### 🔍 Server & Users (`cogs/discord_info.py`)
| Command | Description |
|---|---|
| `avatar [user]` | Show avatar |
| `roles` | List server roles |
| `joinedat [user]` | When a member joined |
| `mods` | Online staff |
| `server` | Server info |
| `user [user]` | User info (uses tracked message count) |

### 💬 Message Stats (`cogs/msg_stats.py`)
| Command | Description |
|---|---|
| `firstmessage [user]` | Find a user's first message in this server |
| `msgleaderboard` | Top 10 chatters in this server |

State: `data/msg_counts.json` — flushed to disk every 2 minutes.

### 🃏 Blackjack (`cogs/blackjack.py`)
| Command | Description |
|---|---|
| `blackjack [bet]` | Play a hand (Hit / Stand / Double) |
| `bjbalance [user]` | Check chip balance |
| `bjdaily` / `blackjackdaily` | Claim 500 free chips (UTC daily) |
| `bjleaderboard` | 🏆 **Global** chip leaderboard |

State: `data/bj_chips.json`, `data/bj_daily.json`. Default starting balance: **1000** chips.

### 🛡️ Moderation (`cogs/mod.py`)
`kick`, `ban`, `unban`, `mute`, `unmute`, `timeout`, `untimeout`,
`masskick`, `massban`, `nickname`, `purge`, `slowmode`,
`lock`, `unlock`, `hide`, `unhide`, `find`, `announcerole`, `role`

### ⚠️ Warnings (`cogs/warns.py`)
`warn @user <reason>`, `warnings [user]`, `clearwarn @user [index|all]`

State: `data/warns.json`.

### 📋 Logging (`cogs/logging_cog.py`)
| Command | Description |
|---|---|
| `setlog #channel` | Send server events to this channel |
| `unsetlog` | Disable logging |

Logs joins, leaves, message edits/deletes, role changes, channel changes, etc.

### 🔐 Encryption (`cogs/encryption.py`)
`encode <type> <text>` / `decode <type> <text>` — types: `base16`, `base32`, `base64`, `base85`, `ascii85`, `hex`, `rot13`.

### 🎭 Button Roles (`cogs/buttonroles.py`)
| Command | Description |
|---|---|
| `buttonroles #channel <emoji1>:@role1 <emoji2>:@role2 …` | Send a persistent role-picker message |

Buttons survive restarts (registered in `cog_load`).

### 🤖 AI Chat (`cogs/ai.py`) 🔒 (model/system changes are owner-only)

**Groq backend** (free + very fast):

| Command | Description |
|---|---|
| `ask <question>` | One-shot Q&A. Attach images / text files for context. |
| `chat <message>` | Conversational; remembers the last N exchanges per channel. |
| `aiclear` | Wipe this channel's Groq memory |
| `aimodel [model]` | View / switch Groq model 🔒 |
| `aisystem [prompt]` | View / set system prompt 🔒 |
| `aimemory [n]` | View / set memory depth 🔒 |
| `aistats` | Session usage |

**OpenRouter backend** (paid, GPT-4o / Claude / Gemini):

| Command | Description |
|---|---|
| `cask <question>` | Same as `ask`, via OpenRouter |
| `cchat <message>` | Same as `chat`, via OpenRouter |
| `cclear` | Wipe OpenRouter memory |
| `caimodel [model]` | View / switch OpenRouter model 🔒 |
| `caisystem [prompt]` | System prompt 🔒 |
| `caimemory [n]` | Memory depth 🔒 |
| `caistats` | Session usage |

**Attachments** — Both backends accept image attachments (PNG/JPEG/GIF/WEBP) on
vision-capable models, and any plain-text file (`.py`, `.txt`, `.json`, `.md`,
`.log`, …) up to 32 KB as additional context.

### 🛡️ Paladin — Antinuke + AutoMod (`cogs/paladin.py`) 🔒 server-owner only

**Antinuke** — limits per actor within a sliding window. When a threshold is hit, Paladin strips the actor's roles **and** triggers Reversion.

```
paladin enable | disable
paladin status
paladin set <event> <count> <window_seconds>
   events: ban, kick, channel_create, channel_delete, role_create, role_delete,
           guild_update, bot_add, webhook
paladin reset <event>
paladin actions   # show recent triggered actions
```

**AutoMod**

```
automod enable | disable
automod status
automod bannedwords add | remove | list <word>
automod antiinvite on|off
automod mentions <limit>            # 0 to disable
automod spam <messages> <seconds>   # message-rate limit
automod punish <warn|mute|kick|ban> <strikes_required> [timeout_minutes]
automod warnexpire <seconds>
```

**Whitelist & Alerts**

```
whitelist @user           # toggle exemption
whitelistshow             # list current whitelist
alertchannel #channel     # where Paladin posts incident embeds
```

### ⚡ Reversion (`cogs/reversion.py`)

No commands — runs automatically when Paladin fires. Re-creates deleted
channels/roles, unbans victims, DMs kicked members an invite, restores
guild settings, and removes unauthorized bots/webhooks.

### 🎟️ Tickets (`cogs/tickets.py`)
| Command | Description |
|---|---|
| `ticket setup #panel @support [#log]` | Drop a dropdown ticket panel |
| `ticket add @user` | Add a user to the current ticket |
| `ticket remove @user` | Remove a user from the current ticket |
| `ticket close` | Close + transcript |

Tickets ship with **Claim** and **Close** buttons. Transcripts are exported via
`chat_exporter` to the configured log channel.

### ⚙️ Admin (`cogs/admin.py`) 🔒
| Command | Description |
|---|---|
| `load <cog>` | Load a cog |
| `unload <cog>` | Unload a cog |
| `reload <cog>` | Reload a cog |
| `reloadall` | Reload every cog |
| `reloadutils <name>` | Reload a `utils/*.py` module |
| `dm <user_id> <message>` | DM a user via the bot |
| `announce #channel <message>` | Send an announcement |
| `change username <name>` | Change the bot's username |
| `change avatar <url>` | Change the bot's avatar |

---

## 🧠 Data & Persistence

All runtime state lives in plain JSON under `data/`:

| File | Cog | Contents |
|---|---|---|
| `bj_chips.json` | Blackjack | `{ user_id: chips }` |
| `bj_daily.json` | Blackjack | `{ user_id: "YYYY-MM-DD" }` |
| `msg_counts.json` | MsgStats | `{ guild_id: { user_id: count } }` |
| `warns.json` | Warns | `{ guild_id: { user_id: [warns…] } }` |
| `tickets.json` | Tickets | `{ guild_id: {…cfg, open_tickets} }` |
| `buttonroles.json` | ButtonRoles | persisted message ↔ role mappings |
| `log_channels.json` | Logging | `{ guild_id: channel_id }` |
| `paladin.json`, `paladin_automod.json`, `paladin_alertnuke.json`, `paladin_whitelist.json` | Paladin | per-guild config + state |
| `antinuke.json`, `bot_logs.json` | Paladin | event history |
| `giveaways.json`, `reminders.json` | Extras | scheduled state |

Caches are flushed on a timer **and** on graceful shutdown. Every JSON write
is atomic (`tmp` + `os.replace`).

---

## 🔧 Hot Reload Workflow

```
!reload extras           # reload one cog
!reloadall               # reload all
!reloadutils permissions # reload a utils module
```

You can iterate on a single cog without dropping the gateway connection.

---

## 🧯 Troubleshooting

| Symptom | Fix |
|---|---|
| Slash commands don't appear | Wait up to 1 hour for global sync, or re-invite the bot with the `applications.commands` scope |
| `LoginFailure` | Bad / regenerated token |
| AI commands say "Network error" | Missing/invalid `GROQ_API_KEY` or `OPENROUTER_API_KEY` |
| Paladin doesn't fire | Run `paladin enable`; check actor isn't whitelisted/owner |
| Tickets don't open | Run `ticket setup` first |
| `MissingPermissions` on mod cmds | Move the bot's role above the target role |

---

## 🤝 Contributing

PRs welcome! Please:

1. Add new features as a **cog** under `cogs/`
2. Keep all I/O async-safe (use `asyncio.Lock` for shared state)
3. Add a `help_embed(prefix)` method or fall back to docstrings
4. Persist state under `data/` with atomic `tmp` + `os.replace` writes

---

## 📜 License

MIT — do whatever, just don't blame us.
