# UmaBot

UmaBot provides Umamusume lookups, support card stats, news, and release alerts in Discord.

## Set up your fork

1. Create your own Discord application and get its bot token. Install the app in your server with the `bot` and `applications.commands` scopes. Give it permission to view its posting channel, send messages, embed links, and attach files. [Discord Developer Portal](https://discord.com/developers/applications)
2. From the repository folder, install the dependencies and start the bot. On Windows PowerShell, use the commands below.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m umabot
```

On first launch, the terminal asks for a bot token if needed and hides it as you enter it. On Windows, UmaBot saves it encrypted for your Windows account after Discord accepts the login. On other operating systems, use `DISCORD_TOKEN` to avoid entering the token on each launch.

For unattended hosting, supply `DISCORD_TOKEN` through your host's secret settings. It takes precedence over the saved token.

## Project layout

- `umabot/` contains the Discord client, startup configuration, alert delivery, and embeds.
- `umabot/commands/` contains the slash commands.
- `umabot/game/` contains the game data clients, shared feed cache, and condition formatting.
- `data/` is created as needed for new databases and ignored by Git. Existing databases in the repository root are picked up in place.

The original `python uma_bot.py` launcher still works. Existing Windows token files are read from the repository root.

## Using the bot

UmaBot registers `/uma` in each server it joins. Run `/uma setup` in each server that wants automated posts and pings. Each server has its own posting channel and subscriptions. Existing single-server installations can use their old `DISCORD_GUILD_ID` to associate the legacy database with that server if UmaBot cannot identify the server from the database.

Run `/uma ping` to mention yourself in the current channel and measure how long UmaBot takes to reply. Use `/uma help` for the full command list.

Use **Stats** on a support card to see its game data at the level cap for each limit break from **0LB** through **4LB**.

## Events and reminders

- `/uma cm` shows the active EN Champions Meeting or the next one. When the EN schedule runs out, it shows a labeled GameTora preview. Search by name to look up a past cup.
- `/uma events` shows active and upcoming EN events, limited missions, and standard banners from GameTora.
- Use `/uma alert` to choose a daily delivery time in the bot host's local time or receive alerts as soon as they qualify. Enter a custom time such as `5:30 PM` and choose how soon before an event ends to get a reminder, from 1 to 168 hours. A scheduled reminder is sent only if the event qualifies at the chosen time, and only once per event.
- Use `/uma reminders` to turn event deadline pings on or off. The default window is 24 hours. Server managers can use `/uma news` or `/uma setup` to select the shared posting channel.
