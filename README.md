# UmaBot

UmaBot provides Umamusume lookups, support card stats, news, and release alerts in Discord.

UmaBot is an open-source community project. Everyone is welcome to fork the repository, modify the bot, and open pull requests with fixes, features, or documentation improvements.

UmaBot was built with help from ChatGPT and may contain mistakes. When opening a pull request, please keep this in mind, verify the behavior you’re changing, and describe any issues you found and how you tested your changes.

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

On macOS or Linux, use these commands from the repository folder. For unattended runs, set `DISCORD_TOKEN` in the environment that starts the bot.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m umabot
```

Keep the bot process running to receive Discord commands and deliver scheduled alerts. In Discord, run `/uma help` to confirm the commands are available, then have a member with **Manage Server** run `/uma setup` and select a text channel for automated posts and pings.

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

### Command reference

| Command | What it does |
| --- | --- |
| `/uma character` | Look up a character by name or numeric ID. |
| `/uma support`, `/uma cards` | Find a support card by card or character name. |
| `/uma skill`, `/uma unique` | Find a shared skill or a trainee's unique skill. |
| `/uma track` | Show a racetrack and its courses, optionally filtered by distance in meters. |
| `/uma birthdays` | Show today's character birthdays. |
| `/uma cm`, `/uma events` | Check the Champions Meeting schedule, EN events, missions, and banners. |
| `/uma subscriptions` | View or remove your character and support card subscriptions by category. |
| `/uma alert`, `/uma reminders` | Set your alert delivery time and event reminder window, or turn deadline pings on or off. |
| `/uma setup`, `/uma news` | Set the shared posting channel. Requires **Manage Server**. |
| `/uma feedback` | Send feedback privately to the bot owner. |
| `/uma help`, `/uma ping` | List commands or measure the bot's reply time. |

## Events and reminders

- `/uma cm` shows the active EN Champions Meeting or the next one. When the EN schedule runs out, it shows a labeled GameTora preview. Search by name to look up a past cup.
- `/uma events` shows active and upcoming EN events, limited missions, and standard banners from GameTora.
- Use `/uma alert` to choose a daily delivery time in the bot host's local time or receive alerts as soon as they qualify. Enter a custom time such as `5:30 PM` and choose how soon before an event ends to get a reminder, from 1 to 168 hours. A scheduled reminder is sent only if the event qualifies at the chosen time, and only once per event.
- Use `/uma reminders` to turn event deadline pings on or off. The default window is 24 hours. Server managers can use `/uma news` or `/uma setup` to select the shared posting channel.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The bot fails to log in | Check that you supplied the bot token from the Discord application. If you rotated the token, update `DISCORD_TOKEN` wherever you run the bot. |
| The bot is online but `/uma` is missing | Confirm the app is installed in the intended server with the scopes above. Check the bot logs for a command sync error and restart after correcting the installation. |
| Automated posts are missing | Use `/uma setup` to check the selected channel. Verify the bot can view the channel, send messages, embed links, and attach files there. |
| A deadline ping is missing | Check `/uma reminders` and `/uma alert`. For scheduled delivery, the event must be within your reminder window at the selected time in the bot host's local time. |
| A lookup or feed fails | Check the bot logs and network access to Umapyoi and GameTora. |

## Contributing

Test changes in a Discord server you control using your own bot application. Start UmaBot with `python -m umabot` from your virtual environment, run the commands affected by your change, and verify any affected buttons or scheduled messages. In your pull request, describe what changed, what you tested, and any known limitations. Keep Discord tokens, local databases, and caches out of commits.
