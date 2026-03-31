# ERLC Session Bot

A standalone Python Discord bot for simple ERLC session management.

## Commands

- `/ssu`
- `/ssu vote_count:5`
- `/ssu vote_count:5 ping:@everyone`
- `/ssd`

`/ssu` starts the session message in your configured session channel. `vote_count` is an optional starting count, and players can then press the vote button to add or remove their own vote.

## What it does

- Sends a session start embed when `/ssu` is used
- Posts the session message in one configured session channel
- Lets players press a button to vote and press again to unvote
- Updates the session message's ERLC player count every 30 seconds
- Ends the session and edits the same embed when `/ssd` is used
- Keeps one active session per Discord server
- Persists the active session so updates can resume after a restart

## Player Count

This bot fetches the actual ERLC in-game player count from the public PRC Private Server API `GET /v1/server` response and displays that live value in the session embed.

## Access

Someone can use `/ssu` and `/ssd` if any one of these is true:

- Their user ID is listed in `OWNER_USER_IDS`
- They have a role listed in `SESSION_MANAGER_ROLE_IDS`
- They already have the Discord `Manage Messages` permission

## Setup

1. Copy `.env.example` to `.env`.
2. Fill in your bot token, your ERLC private server key, your session channel ID, and any role or owner IDs you want to allow.
3. Install dependencies:

```bash
py -m pip install -r requirements.txt
```

4. Start the bot:

```bash
py bot.py
```

## Notes

- `ERLC_SERVER_KEY` comes from your ERLC private server settings and is required for live player counts.
- `SESSION_CHANNEL_ID` is the Discord channel where the session message should always be posted.
- `ERLC_GLOBAL_API_KEY` is optional. Leave it blank unless you were issued one by PRC.
- If you set `ERLC_API_BASE_URL`, point it at the base server endpoint like `https://api.policeroleplay.community/v1/server`, not a deeper path like `/status`.
- `ERLC_HTTP_USER_AGENT` is optional. It lets you override the HTTP user-agent if your host gets blocked by an edge security check.
- If `REGISTER_GUILD_ID` is set, slash commands sync to that server quickly.
- The `ping` option accepts `@everyone`, `@here`, a role mention like `<@&123>`, or a raw role ID.
- A [Procfile](/C:/Users/heher/Documents/Playground/erlc-session-bot/Procfile) is included so platforms like Railway can start the bot with `python bot.py`.
