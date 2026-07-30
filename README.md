# 🐟 KoiLa

> A powerful, modular, self-hostable Discord bot built with **Python 3.11+** and **discord.py 2.x**.

KoiLa combines moderation, antinuke protection, AI, tickets, stock trading, invite tracking, logging, reports, button roles, and dozens of utility commands into one highly configurable bot.

Supports **both slash commands (`/`) and prefix commands (`!`)**.

---

## ✨ Features

### 🛡️ Security

- Paladin Antinuke
- Automatic Reversion
- AutoMod
- Raid Protection
- Anti Invite
- Spam Detection
- Mass Mention Detection
- Whitelist System

---

### 🤖 AI

- Groq Integration
- OpenRouter Integration
- Vision Support
- File Analysis
- Channel Memory
- Configurable Models
- Custom System Prompts

---

### 🎟️ Server Management

- Ticket System
- Reports
- Moderation
- Logging
- Button Roles
- Warnings
- Invite Tracker

---

### 📈 Economy

Unlike traditional Discord economy bots, KoiLa includes a realistic **paper trading simulator** powered by live Yahoo Finance prices.

Trade stocks using virtual **CredCoins (CC)**.

Features include:

- Buy Stocks
- Sell Stocks
- Portfolio Tracking
- Live Prices
- Leaderboards
- Net Worth Calculation

---

### 🎮 Fun

- Trivia
- Polls
- AFK
- Giveaways
- Dice
- Coin Flip
- Slots
- 8Ball
- Random Facts
- Hot Calculator
- Password Generator
- TicTacToe
- Reminders

---

### 🔐 Utilities

- Encryption
- Base64
- Base32
- Base16
- ROT13
- ASCII85
- Hex
- User Lookup
- Server Info
- Avatar
- Message Statistics

---

## 🚀 Quick Start

### Requirements

- Python 3.11+
- Discord Bot Token

Optional

- Groq API Key
- OpenRouter API Key

---

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/KoiLa.git

cd KoiLa

pip install -r requirements.txt
```

---

Create `.env`

```env
DISCORD_TOKEN=YOUR_TOKEN

DISCORD_OWNER_IDS=123456789

GROQ_API_KEY=

OPENROUTER_API_KEY=
```

---

Enable these intents in the Discord Developer Portal.

✅ Presence Intent

✅ Server Members Intent

✅ Message Content Intent

---

Run the bot.

```bash
python index.py
```

Expected output

```text
[INFO] Initialising bot...

Loaded 20+ cogs

Slash commands synced

Logged in as KoiLa
```

---

## 📂 Project Structure

```text
KoiLa/

├── cogs/
├── utils/
├── data/
├── index.py
├── requirements.txt
└── .env
```

Each feature is its own cog and can be reloaded without restarting the bot.
# 📚 Command Reference

Default prefix: `!`

Most commands are also available as slash commands (`/`).

---

# 🎮 Fun Commands

Located in `cogs/fun.py`

| Command | Description |
|----------|-------------|
| `8ball <question>` | Ask the magic 8-ball a question |
| `coinflip` | Flip a coin |
| `dice [sides]` | Roll a dice (default 6 sides) |
| `slot` | Play the slot machine |
| `roulette` | Russian roulette |
| `rate <thing>` | Rates anything from 0–10 |
| `reverse <text>` | Reverse text |
| `password [length]` | Generate a secure password |
| `hotcalc <user1> <user2>` | Compatibility calculator |
| `randomfact` | Random interesting fact |
| `duck` | Random duck image |
| `cat` | Random cat image |
| `dog` | Random dog image |
| `coffee` | Give someone coffee |
| `beer` | Give someone a beer |

---

# ⭐ Extra Commands

Located in `cogs/extras.py`

| Command | Description |
|----------|-------------|
| `snipe` | Show the most recently deleted message |
| `poll` | Create a poll |
| `choose <a,b,c>` | Randomly choose an option |
| `remindme <time> <message>` | Create a reminder |
| `reminders` | List active reminders |
| `tictactoe @user` | Play Tic Tac Toe |
| `trivia` | Random trivia question |
| `giveaway <duration> <prize>` | Start a giveaway |
| `afk [reason]` | Set AFK status |

---

# 📊 Information Commands

Located in `cogs/info.py`

| Command | Description |
|----------|-------------|
| `ping` | Bot latency |
| `about` | Information about KoiLa |
| `invite` | Invite link |
| `covid <country>` | COVID statistics |

---

# 👤 Discord Information

Located in `cogs/discord_info.py`

| Command | Description |
|----------|-------------|
| `server` | Server information |
| `user [member]` | User information |
| `avatar [member]` | View avatar |
| `roles` | List server roles |
| `joinedat [member]` | Join date |
| `mods` | Online moderators |

---

# 💬 Message Statistics

Located in `cogs/msg_stats.py`

| Command | Description |
|----------|-------------|
| `firstmessage [member]` | Find a user's first message |
| `msgleaderboard` | Most active members |

Message statistics are automatically updated and saved every few minutes.

---
# 🛡️ Moderation

Located in `cogs/mod.py`

KoiLa includes a complete moderation suite for everyday server management.

| Command | Description | Permission |
|----------|-------------|------------|
| `kick <member> [reason]` | Kick a member | Kick Members |
| `ban <member> [reason]` | Ban a member | Ban Members |
| `unban <user>` | Unban a user | Ban Members |
| `mute <member>` | Mute a member | Moderate Members |
| `unmute <member>` | Remove mute | Moderate Members |
| `timeout <member> <duration>` | Timeout a member | Moderate Members |
| `untimeout <member>` | Remove timeout | Moderate Members |
| `purge <amount>` | Delete messages | Manage Messages |
| `slowmode <seconds>` | Set channel slowmode | Manage Channels |
| `lock` | Lock current channel | Manage Channels |
| `unlock` | Unlock current channel | Manage Channels |
| `hide` | Hide channel from everyone | Manage Channels |
| `unhide` | Restore channel visibility | Manage Channels |
| `nickname <member> <name>` | Change nickname | Manage Nicknames |
| `find <member>` | Locate member information | Moderator |
| `announcerole <role> <message>` | Mention a role with a message | Manage Roles |
| `role <member> <role>` | Add or remove a role | Manage Roles |
| `masskick` | Kick multiple members | Administrator |
| `massban` | Ban multiple members | Administrator |

---

## ⚠️ Warning System

Located in `cogs/warns.py`

Track rule violations without immediately punishing members.

| Command | Description |
|----------|-------------|
| `warn @user <reason>` | Warn a member |
| `warnings [user]` | View warnings |
| `clearwarn @user <index>` | Remove a specific warning |
| `clearwarn @user all` | Remove all warnings |

Warnings are stored per server and persist across restarts.

---

## 📋 Logging

Located in `cogs/logging_cog.py`

Keep a complete audit trail of server activity.

### Logged Events

- Member joins
- Member leaves
- Message edits
- Message deletions
- Channel creation
- Channel deletion
- Role creation
- Role deletion
- Role updates
- Nickname changes
- Moderation actions

### Commands

| Command | Description |
|----------|-------------|
| `setlog #channel` | Set logging channel |
| `unsetlog` | Disable logging |

Once configured, KoiLa automatically sends rich embeds for supported events.

---

## 👮 Moderator Features

Moderators also benefit from:

- Fast moderation commands
- Rich logging embeds
- Warning history
- User lookup
- Join history
- Message statistics
- Integration with Paladin incident logs

---
# 🤖 AI Chat

Located in `cogs/ai.py`

KoiLa includes two powerful AI providers, allowing you to choose between a fast, free backend or premium frontier models.

---

## Supported Providers

### ⚡ Groq

Fast, free inference for everyday conversations.

Supports models such as:

- Llama
- Gemma
- DeepSeek
- Qwen
- Mixtral
- Other Groq-supported models

---

### 🧠 OpenRouter

Access hundreds of premium AI models including:

- GPT-4o
- GPT-5 (when available)
- Claude
- Gemini
- DeepSeek
- Qwen
- Llama
- Mistral

---

# Features

- Persistent conversation memory
- Vision support
- Image understanding
- Text file analysis
- Configurable system prompts
- Configurable memory depth
- Per-channel conversations
- Model switching
- Usage statistics

---

# Groq Commands

| Command | Description |
|----------|-------------|
| `ask <question>` | One-time AI question |
| `chat <message>` | Continue a conversation |
| `aiclear` | Clear conversation history |
| `aimodel` | View current model |
| `aimodel <model>` | Change model *(Owner)* |
| `aisystem` | View current system prompt |
| `aisystem <prompt>` | Set system prompt *(Owner)* |
| `aimemory` | View memory size |
| `aimemory <number>` | Change memory depth *(Owner)* |
| `aistats` | View AI usage statistics |

---

# OpenRouter Commands

| Command | Description |
|----------|-------------|
| `cask <question>` | One-time question |
| `cchat <message>` | Chat with AI |
| `cclear` | Clear conversation |
| `caimodel` | View model |
| `caimodel <model>` | Change model *(Owner)* |
| `caisystem <prompt>` | Change system prompt *(Owner)* |
| `caimemory <number>` | Set memory depth *(Owner)* |
| `caistats` | Usage statistics |

---

# Supported Attachments

The AI can analyse attached files.

### Images

- PNG
- JPG
- JPEG
- WEBP
- GIF

### Text Files

- `.txt`
- `.py`
- `.json`
- `.js`
- `.ts`
- `.java`
- `.cpp`
- `.md`
- `.log`
- `.html`
- `.css`
- `.yml`
- `.yaml`

Maximum file size: **32 KB**

---

# 🔐 Encryption

Located in `cogs/encryption.py`

Encode and decode data using multiple formats.

---

## Supported Formats

- Base16
- Base32
- Base64
- Base85
- ASCII85
- Hex
- ROT13

---

## Commands

| Command | Description |
|----------|-------------|
| `encode <type> <text>` | Encode text |
| `decode <type> <text>` | Decode text |

Example:

```text
!encode base64 Hello World
```

---

# 🎭 Button Roles

Located in `cogs/buttonroles.py`

Allow members to assign roles by clicking buttons.

Unlike reaction roles, button roles continue working after restarts.

---

## Features

- Persistent buttons
- Unlimited roles
- Multiple role panels
- Restart-safe
- Fast interaction

---

## Commands

| Command | Description |
|----------|-------------|
| `buttonroles #channel <emoji>:<role>` | Create a role panel |

Example

```text
!buttonroles #roles

🎮:@Gamer

🎵:@Music

💻:@Developer

🎨:@Artist
```

Members can simply press the buttons to receive or remove roles.

---
# 🎟️ Ticket System

Located in `cogs/tickets.py`

KoiLa provides a fully-featured ticket system designed for support teams, staff, and customer service.

Create beautiful ticket panels, claim tickets, generate transcripts, and manage everything directly from Discord.

---

## Features

- Dropdown ticket panels
- Claim button
- Close button
- HTML transcripts
- Support roles
- Ticket logs
- Multi-user support
- Persistent panels
- Restart-safe

---

## Commands

| Command | Description |
|----------|-------------|
| `ticket setup #panel @support [#logs]` | Create a ticket panel |
| `ticket add @user` | Add a member to the current ticket |
| `ticket remove @user` | Remove a member from the current ticket |
| `ticket close` | Close the current ticket |

---

## Ticket Workflow

1. User opens a ticket from the dropdown panel.
2. A private support channel is created.
3. Staff can **claim** the ticket.
4. Additional users may be added or removed.
5. Closing the ticket generates an HTML transcript.
6. The transcript is automatically sent to the configured log channel.

---

## Buttons

Every ticket includes interactive buttons:

- ✅ Claim
- 🔒 Close

No commands are required after opening the ticket.

---

# 📋 Reports

Located in `cogs/reports.py`

KoiLa includes a built-in reporting system that allows users to safely report rule violations through Direct Messages.

Reports are automatically forwarded to a configured staff channel with all provided evidence.

---

## Features

- DM-based reporting
- Guided report wizard
- Evidence collection
- Staff report channel
- Anonymous-friendly workflow
- Clean embed formatting

---

## Commands

| Command | Description |
|----------|-------------|
| `setreport [#channel]` | Configure the server's report channel |
| `reportinfo` | View the configured report channel |
| `clearreport` | Remove the configured report channel |
| `report` | Begin submitting a report through DMs |

---

## Reporting Process

1. User runs `!report`
2. The bot opens a DM conversation.
3. The user is prompted for:
   - Reported user
   - Reason
   - Description
   - Evidence (optional)
4. The report is formatted into a rich embed.
5. Staff receive the report in the configured reports channel.

---

## Supported Evidence

Users may attach:

- Images
- Videos
- Screenshots
- Message links
- Text files

Attachments are forwarded together with the report whenever possible.

---

## Staff Benefits

- Organized reports
- Faster moderation
- Permanent report history
- Easy evidence review
- Configurable destination channel

---
# 🛡️ Paladin — Antinuke & AutoMod

Located in `cogs/antinuke.py` and `cogs/automod.py`

Paladin is KoiLa's advanced server protection system designed to stop raids, nukes, malicious staff, compromised accounts, and rogue bots before they can damage your server.

Unlike traditional moderation bots, Paladin actively detects suspicious behavior, neutralizes the attacker, and works together with **Reversion** to undo unauthorized changes.

---

# Features

### 🚫 Antinuke Protection

Protects against:

- Mass bans
- Mass kicks
- Channel deletion
- Channel creation spam
- Role deletion
- Role creation spam
- Guild setting changes
- Unauthorized bot additions
- Webhook creation
- Webhook abuse

---

### 🤖 AutoMod

Built-in moderation features include:

- Anti Invite
- Banned Words
- Mention Spam Protection
- Message Spam Detection
- Strike System
- Automatic Punishments
- Warning Expiration

---

### 🛡️ Whitelist System

Trusted users can be exempt from protection.

Ideal for:

- Owners
- Administrators
- Trusted Developers
- Automation Bots

---

# Commands

## Enable / Disable

| Command | Description |
|----------|-------------|
| `paladin enable` | Enable Paladin |
| `paladin disable` | Disable Paladin |
| `paladin status` | View current configuration |

---

## Configure Limits

Set thresholds for protected events.

| Command | Description |
|----------|-------------|
| `paladin set ban <count> <seconds>` | Ban protection |
| `paladin set kick <count> <seconds>` | Kick protection |
| `paladin set channel_create <count> <seconds>` | Channel creation limit |
| `paladin set channel_delete <count> <seconds>` | Channel deletion limit |
| `paladin set role_create <count> <seconds>` | Role creation limit |
| `paladin set role_delete <count> <seconds>` | Role deletion limit |
| `paladin set guild_update <count> <seconds>` | Guild update limit |
| `paladin set bot_add <count> <seconds>` | Bot add protection |
| `paladin set webhook <count> <seconds>` | Webhook protection |

---

## Reset Limits

| Command | Description |
|----------|-------------|
| `paladin reset <event>` | Reset one event back to defaults |

---

## Activity

| Command | Description |
|----------|-------------|
| `paladin actions` | Show recent Paladin actions |

---

# AutoMod Commands

| Command | Description |
|----------|-------------|
| `automod enable` | Enable AutoMod |
| `automod disable` | Disable AutoMod |
| `automod status` | View AutoMod status |
| `automod antiinvite on` | Enable invite filtering |
| `automod antiinvite off` | Disable invite filtering |
| `automod mentions <limit>` | Maximum mentions allowed |
| `automod spam <messages> <seconds>` | Configure spam detection |
| `automod warnexpire <seconds>` | Warning expiration time |

---

## Banned Words

| Command | Description |
|----------|-------------|
| `automod bannedwords add <word>` | Add a banned word |
| `automod bannedwords remove <word>` | Remove a banned word |
| `automod bannedwords list` | View banned words |

---

## Punishments

Configure what happens after repeated violations.

| Command | Description |
|----------|-------------|
| `automod punish warn <strikes>` | Warn user |
| `automod punish mute <strikes> [minutes]` | Timeout user |
| `automod punish kick <strikes>` | Kick user |
| `automod punish ban <strikes>` | Ban user |

---

# Whitelist

Trusted members bypass Paladin protections.

| Command | Description |
|----------|-------------|
| `whitelist @user` | Toggle whitelist status |
| `whitelistshow` | List all whitelisted users |

---

# Alert Channel

Receive detailed incident reports whenever Paladin activates.

| Command | Description |
|----------|-------------|
| `alertchannel #channel` | Configure the alert channel |

Incident logs include:

- Triggered protection
- Responsible user
- Timestamp
- Action taken
- Server information

---

# ⚡ Reversion

Located in `cogs/reversion.py`

Reversion automatically restores changes made by malicious users after Paladin detects an attack.

No commands are required—everything runs automatically.

---

## Automatically Restores

- Deleted channels
- Deleted roles
- Guild settings
- Server icon
- Server name
- Permissions
- Webhooks
- Unauthorized bots
- Members banned during a raid

---

## Additional Recovery

Reversion can also:

- Remove malicious webhooks
- Kick unauthorized bots
- Restore deleted roles
- Restore deleted channels
- Unban innocent users
- Recreate server structure

---

# How It Works

1. Malicious activity is detected.
2. Paladin identifies the responsible user.
3. Their roles are removed immediately.
4. Reversion restores affected resources.
5. An incident report is sent to the configured alert channel.

This provides near-instant protection against most common server nuking techniques.

---
# 📨 Invite Tracker

Located in `cogs/invite_tracker.py`

KoiLa includes a powerful invite tracking system that accurately attributes joins, tracks invite history, detects potential alt accounts, and provides detailed leaderboards.

Unlike basic invite bots, KoiLa stores historical data, supports manual adjustments, and prevents inaccurate invite attribution whenever possible.

---

# Features

- Accurate invite tracking
- Invite leaderboards
- Invite history
- Invite lookup
- Alt account detection
- Manual invite adjustments
- Invite resync
- Server reset
- Member reset
- Join & leave tracking
- Configurable alert system

---

# Setup

Configure invite tracking using:

```text
!invitesetup #invite-logs
```

This creates a baseline of all existing invites.

Only invites used **after setup** are counted.

---

# Commands

## General

| Command | Description |
|----------|-------------|
| `invites [member]` | View a member's invite count |
| `invitedby [member]` | See who invited a member |
| `invitedlist [member]` | Members invited by someone |
| `inviteleaderboard` | Top inviters |
| `invitehistory [member]` | Invite history |

---

## Alt Detection

Accounts younger than the configured threshold are marked as **Suspected Alts** until reviewed.

| Command | Description |
|----------|-------------|
| `invitealts [member]` | View pending alt accounts |
| `invitealt approve <member>` | Approve an alt account |
| `invitealt deny <member>` | Reject an alt account |

---

## Management

| Command | Description |
|----------|-------------|
| `inviteadd <member> <amount>` | Add invite credits |
| `inviteremove <member> <amount>` | Remove invite credits |
| `inviteresync` | Resync all server invites |
| `invitereset member` | Reset one member |
| `invitereset server` | Reset all invite data |

---

# Staff Features

Invite moderators can:

- Review suspected alts
- Manually adjust invite counts
- Restore missing invite data
- View complete invite history
- Audit suspicious joins

---

# 📈 Stock Trading

Located in `cogs/stocks.py`

KoiLa features a realistic **paper trading simulator** powered by live Yahoo Finance market data.

Users receive virtual **CredCoins (CC)** to build portfolios, compete on leaderboards, and learn investing without risking real money.

---

# Features

- Live stock prices
- Buy & sell shares
- Portfolio tracking
- Net worth calculation
- Cash balance
- Leaderboards
- Profit/Loss tracking
- Unlimited paper trading
- Fast price lookups

---

# Starting Balance

Every new user starts with:

```text
10,000 CredCoins (CC)
```

No real money is involved.

---

# Commands

| Command | Description |
|----------|-------------|
| `stockhelp` | Stock trading guide |
| `price <ticker>` | View current market price |
| `buy <ticker> <shares>` | Purchase shares |
| `sell <ticker> <shares>` | Sell owned shares |
| `portfolio` | View holdings |
| `balance` | Cash, investments & net worth |
| `leaderboard` | Richest traders |
| `reset` | Reset portfolio back to 10,000 CC |

---

# Example

```text
!price NVDA

!buy NVDA 5

!portfolio

!sell NVDA 2
```

---

# Supported Stocks

Examples include:

- AAPL
- MSFT
- NVDA
- AMD
- META
- GOOGL
- AMZN
- TSLA
- NFLX
- SPY
- QQQ

…and thousands more available through Yahoo Finance.

---

# Portfolio Information

Your portfolio displays:

- Cash Balance
- Current Holdings
- Average Buy Price
- Current Market Value
- Unrealized Profit/Loss
- Total Net Worth

---

# Leaderboards

Compete against other server members based on total portfolio value.

Leaderboards automatically update as market prices change.

---

# Data Storage

Trading data is stored in:

```text
data/stocks.json
```

Portfolio information is automatically saved and restored after bot restarts.

---# ⚙️ Admin Commands

Located in `cogs/admin.py`

Administrative commands are restricted to bot owners and are designed to simplify development, maintenance, and server management without requiring a bot restart.

---

## Cog Management

| Command | Description |
|----------|-------------|
| `load <cog>` | Load a cog |
| `unload <cog>` | Unload a cog |
| `reload <cog>` | Reload a cog |
| `reloadall` | Reload every loaded cog |
| `reloadutils <module>` | Reload a utility module |

---

## Bot Management

| Command | Description |
|----------|-------------|
| `dm <user_id> <message>` | Send a direct message as the bot |
| `announce #channel <message>` | Send an announcement |
| `change username <name>` | Change the bot's username |
| `change avatar <image_url>` | Change the bot's avatar |

---

# 💾 Data & Persistence

KoiLa stores all runtime data in JSON files inside the `data/` directory.

```
data/
├── antinuke.json
├── bot_logs.json
├── buttonroles.json
├── giveaways.json
├── invite_tracker.json
├── log_channels.json
├── msg_counts.json
├── paladin.json
├── paladin_alertnuke.json
├── paladin_automod.json
├── paladin_whitelist.json
├── reminders.json
├── stocks.json
├── tickets.json
├── warns.json
└── ...
```

---

## Data Overview

| File | Purpose |
|------|---------|
| `stocks.json` | User portfolios and balances |
| `tickets.json` | Ticket configuration and open tickets |
| `warns.json` | Warning database |
| `msg_counts.json` | Message statistics |
| `buttonroles.json` | Persistent role buttons |
| `log_channels.json` | Logging configuration |
| `invite_tracker.json` | Invite tracking database |
| `paladin*.json` | Paladin configuration |
| `reminders.json` | Active reminders |
| `giveaways.json` | Giveaway storage |

All writes are **atomic**, minimizing the risk of corruption during unexpected shutdowns.

---

# 🔄 Hot Reloading

KoiLa is designed around modular cogs, allowing features to be updated without disconnecting the bot.

## Examples

```text
!reload moderation
```

Reload a single cog.

```text
!reload tickets
```

Reload the ticket system.

```text
!reloadall
```

Reload every loaded cog.

```text
!reloadutils permissions
```

Reload a utility module.

This significantly speeds up development by avoiding full bot restarts.

---

# 🚀 Performance

KoiLa is built with scalability in mind.

## Highlights

- Fully asynchronous
- Modular cog architecture
- Atomic file writes
- Cached runtime data
- Restart-safe components
- Persistent UI Views
- Efficient background tasks
- Low memory footprint

---

# 🧯 Troubleshooting

| Problem | Solution |
|----------|----------|
| Slash commands don't appear | Wait for global sync or re-invite with `applications.commands` scope |
| LoginFailure | Verify your bot token |
| Missing permissions | Move the bot role above target roles |
| Ticket panel doesn't work | Run `ticket setup` again |
| Invite tracking isn't working | Run `invitesetup` to create a baseline |
| Stock prices fail | Check your internet connection and Yahoo Finance availability |
| Paladin isn't triggering | Ensure Paladin is enabled and the actor isn't whitelisted |

---

# 🤝 Contributing

Pull requests are always welcome.

When contributing, please:

- Keep features modular by using cogs.
- Follow the existing project structure.
- Use asynchronous APIs where appropriate.
- Store persistent data inside the `data/` directory.
- Avoid blocking the event loop.
- Document new commands before opening a pull request.

If you discover a bug or have a feature request, please open an issue on GitHub.

---

# 📜 License

KoiLa is released under the **MIT License**.

You are free to:

- ✅ Use
- ✅ Modify
- ✅ Fork
- ✅ Distribute
- ✅ Use commercially

Please retain the original license notice in derivative works.

---

# ❤️ Credits

Built with:

- Python 3.11+
- discord.py 2.x
- aiohttp
- chat_exporter
- Yahoo Finance
- Groq
- OpenRouter

Special thanks to everyone who contributes to KoiLa and helps improve the project.

---

# ⭐ Support the Project

If you enjoy KoiLa, consider supporting the project by:

- ⭐ Starring the GitHub repository
- 🍴 Forking the project
- 🐛 Reporting bugs
- 💡 Suggesting new features
- 🤝 Contributing code

Every contribution helps make KoiLa even better.

---
