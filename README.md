# Modex

Modex is a slash-command Discord moderation bot built with Python 3.11+, `discord.py` 2.x, and SQLite. Moderation is manual: the bot only acts when an authorized moderator runs a command. It does not provide AutoMod, automatic moderation, channel logging, or a web dashboard.

**GitHub repository:** <https://github.com/MrGoofyDev/Modex>

## Features

- Slash commands for member moderation and slowmode
- Moderator permission, bot permission, and role-hierarchy checks
- Persistent SQLite cases and warning history with sequential `MODEX-0001` case IDs
- Optional member DMs with clickable server and moderator links
- Public confirmations for member moderation; private `/purge` and `/slowmode` responses
- No paid APIs, external database, or privileged gateway intents

## Commands and permissions

| Command | Required server permission | Behavior |
| --- | --- | --- |
| `/ping` | None | Public latency response. |
| `/help` | None | Public command list. |
| `/ban user [reason]` | Ban Members | Ban and notify the member by DM when possible. |
| `/kick user [reason]` | Kick Members | Kick and notify the member by DM when possible. |
| `/timeout user duration [reason]` | Moderate Members | Timeout for a duration such as `30m`, `2h`, or `7d` (maximum 28 days). |
| `/warn user reason` | Moderate Members | Add a warning and attempt a DM. |
| `/unwarn user [reason]` | Moderate Members | Remove the most recent active warning and attempt a DM. |
| `/purge amount` | Manage Messages | Delete 1–100 recent messages; response is ephemeral. |
| `/slowmode seconds` | Manage Channels | Set channel slowmode from 0 to 21600 seconds; response is ephemeral. |

Discord administrators have all server permissions and can use the moderation commands. Modex also checks its own permissions and role hierarchy; members with equal or higher roles and the server owner cannot be moderated by the bot. Member moderation success messages are public. Permission and other error messages may be private.

Modex attempts member notification DMs for ban, kick, timeout, warn, and unwarn. DM failures do not prevent moderation. For a kick, it opens and sends to the DM channel before removing the member, because Discord can reject a new DM after the bot and member no longer share a server. The public success confirmation still appears only if the kick succeeds.

## Requirements and dependencies

- Python 3.11 or newer
- `discord.py` 2.x (installed from `requirements.txt`)
- SQLite (included with standard Python builds)
- A Discord application and bot token

Install dependencies locally with:

```bash
python -m pip install -r requirements.txt
```

## Discord bot setup

1. Create an application and bot at <https://discord.com/developers/applications>.
2. Set the application name and bot profile name to **Modex** in the Developer Portal. Discord account branding is configured there, not by the source code.
3. Copy the bot token from the application's **Bot** settings. Keep it secret.
4. In **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes.
5. Grant these bot permissions:
   - View Channels
   - Send Messages
   - Embed Links
   - Read Message History
   - Manage Messages
   - Manage Channels
   - Ban Members
   - Kick Members
   - Moderate Members
6. Invite the bot to your server using the generated URL.
7. Put Modex's server role above the roles it must moderate.

No privileged gateway intents need to be enabled.

## Local startup

Set the token in the process environment; Modex does not read `.env` files:

```bash
export DISCORD_TOKEN="YOUR_DISCORD_BOT_TOKEN"
python -m pip install -r requirements.txt
python main.py
```

Modex prints a clear startup error and exits if `DISCORD_TOKEN` is missing. It never prints the token.

## Deploy on Bot-Hosting.net

Bot-Hosting.net describes support for Python projects, GitHub repositories (including private repositories), environment variables, and console logs. Panel labels can change; use the current dashboard instructions if a control is named differently.

1. Sign in at <https://bot-hosting.net/> and create a new project or deployment for a Discord bot/Python application.
2. Connect the GitHub account that can access <https://github.com/MrGoofyDev/Modex>, or upload the project files. Select the `main` branch if prompted.
3. Configure the runtime to use Python 3.11 or newer.
4. Ensure the deployment installs dependencies from `requirements.txt`. If the panel has an install/dependency command, use:

   ```bash
   python -m pip install -r requirements.txt
   ```

5. Add the environment variable `DISCORD_TOKEN` in the hosting platform's environment-variable or secrets settings. Set its value to your bot token and mark it secret/private if that option is provided. Do not put the token in source code, a committed file, or a command that might appear in logs.
6. Set the startup command to:

   ```bash
   python main.py
   ```

7. Start or restart the deployment. Open its console/log view and confirm that the bot logs in and synchronizes its slash commands.
8. Confirm the deployment has persistent storage for the project directory containing `database/modex.db`. If the platform uses an ephemeral filesystem, configure its persistent-volume/storage feature for that path before relying on the bot for moderation history. A temporary filesystem can lose the database after restart or redeployment.
9. Keep an eye on the console for startup errors. The host may restart a crashed process, but it cannot fix a missing token, dependency, permission, or persistent-storage configuration.

These are setup instructions only; they do not mean Modex has been deployed or verified on the hosting platform.

## Environment variable and security

The only required runtime setting is:

```text
DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
```

Set it in the hosting provider's environment settings. Modex reads it with `os.getenv("DISCORD_TOKEN")`. The local `.env` file is not loaded by the application and must never be uploaded or committed. `.gitignore` excludes environment files, credentials, database files, Python caches, and common temporary output.

Never share the token, paste it into source code, or expose it in screenshots or logs. If a token may have been exposed, reset it in the Discord Developer Portal and update the hosting environment variable. Use a private repository if the project should not be publicly visible.

## SQLite database and persistence

On first startup, Modex creates `database/modex.db` and its tables automatically. Existing records are preserved during later startups. Case IDs are allocated persistently and continue beyond `MODEX-9999`. Existing records retain their original `MODBIT-####` case IDs; new case IDs use the `MODEX-####` prefix and continue after the highest recorded case number. Warnings can be repeated; `/unwarn` marks the latest active warning removed rather than erasing its history.

The database is local SQLite storage; it is not uploaded to GitHub. SQLite database files and sidecars (`*.db*`, `*.sqlite*`, and `*.sqlite3*`) are ignored by Git. Ensure the host keeps `database/modex.db` on persistent storage. A temporary or non-persistent filesystem can lose the database and moderation records when the deployment is restarted, moved, or rebuilt.

For manual migration, stop the bot, download the active database, then upload it to the matching path on the new host before restarting. Ensure the destination storage is persistent. No bot backup or restore commands are provided.

For compatibility, if `database/modex.db` is absent but the existing `database/modbit.db` is present, Modex continues to use that file in place. It does not rename, copy, reset, or recreate the legacy file, and it preserves all records. If both files exist, startup stops with an error so you can choose the correct database rather than silently ignoring records.

## Updating from GitHub

To update a deployment, use the hosting platform's supported GitHub redeploy/pull workflow, or upload the updated source files. Restart the bot after updating and check the console. Keep the existing database file on persistent storage; do not replace it with an empty database. Reinstall dependencies from `requirements.txt` if it changed.

## Troubleshooting

- **`DISCORD_TOKEN is not set`:** Add `DISCORD_TOKEN` to the host's environment/secrets settings, save the deployment configuration, and restart.
- **Invalid token / login failure:** Reset or copy the bot token from the Developer Portal, update the environment variable, and restart. Do not put the token in a log or source file.
- **Slash commands missing:** Confirm the bot was invited with both `bot` and `applications.commands` scopes; check startup logs for sync errors.
- **Moderation action denied:** Check the user's required server permission, Modex's permission, and role order. The bot cannot moderate the server owner or an equal/higher role.
- **Purge or slowmode fails:** Check the command user's and bot's permissions in that channel.
- **DM notification unavailable:** The recipient may block DMs or Discord may reject the request. The moderation action is designed to proceed regardless.
- **History disappears after a host restart:** Check whether the bot's project directory/database path is persistent. Restore the database from your own manually stored copy if available.
- **Dependency/import error:** Check the Python version and confirm the host installed `requirements.txt`.
