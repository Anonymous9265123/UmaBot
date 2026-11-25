# UmaBot

UmaBot provides Umamusume lookups, support card stats, news, and release alerts in Discord.

UmaBot is an open-source community project. Everyone is welcome to fork the repository, modify the bot, and open pull requests with fixes, features, or documentation improvements. [MIT License](LICENSE)

UmaBot was built with help from ChatGPT and may contain mistakes. When opening a pull request, please keep this in mind, verify the behavior you’re changing, and describe any issues you found and how you tested your changes.

## Run your own UmaBot

You need Python 3.11 or newer and a Discord server where you can install apps. CI covers Python 3.11 on Ubuntu and Python 3.13 on Ubuntu and Windows.

1. **Get the code.** Use **Code → Download ZIP** and extract it, or run `git clone https://github.com/Anonymous9265123/UmaBot.git`. If you're contributing or want your own copy on GitHub, fork the repo first and clone your fork.
2. **Create a Discord application.** Select **New Application**, open the **Bot** page, and generate or copy the **bot token**. Keep it private. [Discord Developer Portal](https://discord.com/developers/applications)
3. **Install the app in your server.** On the application's **Installation** page, enable **Guild Install** and select **Discord Provided Link**. Under **Default Install Settings** for **Guild Install**, add `bot` and `applications.commands` with **View Channel**, **Send Messages**, **Embed Links**, and **Attach Files**. Open the install link and select **Add to server**. [Discord installation guide](https://docs.discord.com/developers/quick-start/getting-started#installing-your-app)
4. **Start UmaBot.** Open a terminal in the folder containing `setup_umabot.py` and run the command for your OS.

| OS | Command |
| --- | --- |
| Windows PowerShell | `py -3 setup_umabot.py` |
| macOS or Linux | `python3 setup_umabot.py` |

The script creates `.venv`, installs dependencies, and starts the bot. Paste the bot token when prompted. The terminal hides your input. In Discord, run `/uma help` to confirm the bot is ready. For automated posts and pings, have someone with **Manage Server** run `/uma setup` and choose a text channel.

### Start again or run on a host

Keep the bot process running to receive commands and send alerts. Run the same setup command after pulling changes to install any updated dependencies. Add `--install-only` to prepare the environment without connecting to Discord.

On Windows, UmaBot saves an interactively entered token encrypted for your Windows account after a successful login. On macOS or Linux, you can enter the token at startup or supply it through `DISCORD_TOKEN`. For unattended hosting on any OS, configure `DISCORD_TOKEN` in your host's secret settings and start the bot with `python -m umabot` using the environment's Python. An environment token takes precedence over the saved Windows token.

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
| `/uma compare` | Pick the first card, its limit break, the second card, then its limit break (defaults to 4LB). Shared stats appear first, followed by stats active on only one card. Select **Card details** for unique effects. |
| `/uma skill`, `/uma unique` | Find a shared skill or a trainee's unique skill. |
| `/uma track` | Show a racetrack and its courses, optionally filtered by distance in meters. |
| `/uma birthdays` | Show today's character birthdays. |
| `/uma cm`, `/uma events` | Check the Champions Meeting schedule, EN events, missions, and banners. |
| `/uma banner` | Show active EN trainee and support card banners, featured picks, and end times. |
| `/uma upcoming` | List EN starts over the next 30 days, with a separate GameTora forecast based on the Japanese release schedule. Forecast dates and lineups can change. |
| `/uma digest` | View or configure the weekly EN schedule digest. Requires **Manage Server**. |
| `/uma subscriptions` | View or remove your character and support card subscriptions by category. |
| `/uma alert`, `/uma reminders` | Set your alert delivery time and event reminder window, or turn deadline pings on or off. |
| `/uma setup`, `/uma news` | Set the shared posting channel. Requires **Manage Server**. |
| `/uma feedback` | Send feedback privately to the bot owner. |
| `/uma help`, `/uma ping` | List commands or measure the bot's reply time. |

## Events and reminders

- `/uma cm` shows the active EN Champions Meeting or the next one. When the EN schedule runs out, it shows a labeled GameTora preview. Search by name to look up a past cup.
- `/uma events` shows active and upcoming EN events, limited missions, and standard banners from GameTora.
- Use `/uma digest channel:#uma-news day:Monday delivery_time:09:00` to post a weekly summary of confirmed starts and deadlines for the next seven days. GameTora forecasts appear separately and may change. Delivery uses the bot host's local time and sends no mentions. Run `/uma digest enabled:False` to turn it off.
- Use `/uma alert` to choose a daily delivery time on the computer running UmaBot or receive alerts as soon as they qualify. Enter a custom time such as `5:30 PM` and choose how soon before an event ends to get a reminder, from 1 to 168 hours. Existing schedules keep the same clock time and follow the bot host's time zone. A scheduled reminder is sent only if the event qualifies at the chosen time, and only once per event.
- Use `/uma reminders` to turn event deadline pings on or off. The default window is 24 hours. Server managers can use `/uma news` or `/uma setup` to select the shared posting channel.

## Data sources

- Umapyoi supplies EN news, character profiles and birthdays, related outfits and support cards, and support card details. [Umapyoi API](https://umapyoi.net/docs/)
- GameTora supplies skill and racetrack catalogs, card stats and release data, and EN schedules for events, missions, and banners, including separate forecasts. [GameTora](https://gametora.com/umamusume)

With a posting channel configured, background checks run every 15 minutes for Umapyoi EN news and hourly for card and trainee releases. GameTora’s manifest is cached for an hour, and UmaBot downloads catalog data only when it needs a version it has not cached. Slash commands can look up records on demand and reuse cached catalog data.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The bot fails to log in | Check that you supplied the bot token from the Discord application. If you rotated the token, update `DISCORD_TOKEN` wherever you run the bot. |
| The bot is online but `/uma` is missing | Confirm the app is installed in the intended server with the scopes above. Check the bot logs for a command sync error and restart after correcting the installation. |
| Automated posts are missing | Use `/uma setup` to check the selected channel. Verify the bot can view the channel, send messages, embed links, and attach files there. |
| A deadline ping is missing | Check `/uma reminders` and `/uma alert`. For scheduled delivery, the event must be within your reminder window at the selected time on the computer running UmaBot. |
| A lookup or feed fails | Check the bot logs and network access to Umapyoi and GameTora. |

## Contributing

From the repository root, run the installer to create `.venv`, then install Ruff into that same environment. The checks below also run in GitHub Actions and require no Discord token.

**Windows PowerShell**

```powershell
python setup_umabot.py --install-only
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -c "from umabot.bot import main"
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

**macOS or Linux**

```sh
python3 setup_umabot.py --install-only
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m ruff check .
.venv/bin/python -c "from umabot.bot import main"
.venv/bin/python -m unittest discover -s tests
```

Test changes in a Discord server you control using your own bot application. Start UmaBot with `python -m umabot` from your virtual environment, run the commands affected by your change, and verify any affected buttons or scheduled messages. In your pull request, describe what changed, what you tested, and any known limitations. Keep Discord tokens, local databases, and caches out of commits.
