# UmaBot

UmaBot provides Umamusume lookups, support card stats, news, and release alerts in Discord.

UmaBot is an open-source community project. Everyone is welcome to fork the repository, modify the bot, and open pull requests with fixes, features, or documentation improvements. [MIT License](LICENSE)

UmaBot was built with help from ChatGPT and may contain mistakes. When opening a pull request, please keep this in mind, verify the behavior you’re changing, and describe any issues you found and how you tested your changes.

## Latest update

- Choose **Events** in `/uma subscriptions` to check or remove your EN event announcement subscription. Successful subscription confirmations point you there and to the original alert button.
- UmaBot builds EN news embeds and event buttons only for posts it is about to send.
- Support card lookups use the same EN release status for the display and the alert button. The **Stats** button remains available.

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

After saving the channel, select **Send test post** to verify UmaBot can send an embed there.

### Start again or run on a host

Keep the bot process running to receive commands and send alerts. Run the same setup command after pulling changes to install any updated dependencies. Add `--install-only` to prepare the environment without connecting to Discord.

For unattended hosting, configure `DISCORD_TOKEN` in your host's secret settings and start the bot with `python -m umabot` using the environment's Python. For interactive development, you can also enter the token at startup.

### Deploy on Fade

UmaBot has been deployed successfully on FadeHost using `python -m umabot` as the start command.

In FadeHost, create a **Discord bot** from the UmaBot GitHub repository. Under **Settings → Build & start**, select branch `main`, use Python (auto-detect also works), leave **Build command** blank, and set **Start command** to `python -m umabot`. UmaBot requires Python 3.11 or later. FadeHost installs the root `requirements.txt` during deployment. The repository settings, build and start commands, and environment variables are saved in the dashboard for future redeploys. Check **Deploys** for GitHub auto-deploy. Saving settings can trigger a deploy, and pushes to the connected branch redeploy automatically when auto-deploy is enabled. [FadeHost Discord bot hosting](https://www.fadehost.com/docs/discord-bot-hosting/) · [FadeHost app settings](https://www.fadehost.com/docs/app-hosting/)

| Environment variable | Value |
| --- | --- |
| `DISCORD_TOKEN` | Required. Enter the bot token in FadeHost's Discord bot token field or secret settings. |
| `UMABOT_DATA_DIR` | Set to `/data/storage/umabot` on FadeHost to preserve SQLite data. Optional for local installs. |
| `UMAMOE_API_KEY` | Optional secret for showing Friend ID results from uma.moe in Discord. |

`UMABOT_DATA_DIR` selects the folder. The Discord server ID determines the alert filename, `uma-alerts-<guild_id>.sqlite3`. Feedback stays in `uma-feedback.sqlite3` in the same folder. FadeHost resets the checkout under `/data/app` on deploy, and its existing `/data/storage` volume survives redeploys. If your numbered databases already work in persistent storage, keep the directory setting and remove `DISCORD_GUILD_ID` when upgrading. UmaBot no longer uses it. If you are moving existing databases into persistent storage, stop the bot, back up the files, and copy the numbered alert and feedback databases into the folder where the corresponding destination files are absent. For an unnumbered file, follow the migration steps below. [FadeHost persistent storage](https://www.fadehost.com/docs/app-hosting/#storage-what-survives-a-deploy) · [FadeHost file manager](https://www.fadehost.com/docs/app-files/)

After the deployment starts, look for `UmaBot is online as` in FadeHost's live console and run `/uma help` in Discord to confirm the commands are ready. On the first deployment, use `/uma setup` to configure automated posts. After a redeploy, check `/uma subscriptions` to confirm your existing subscriptions are still present.

### Migrate an older alert database

Older installations may have an unnumbered `uma-alerts.sqlite3` in the configured storage folder, the repository's `data/` folder, or the repository root. UmaBot leaves it in place and logs a startup notice when it finds one.

1. Stop the bot and back up the unnumbered database and any numbered databases before upgrading or copying files. Use a SQLite backup tool if you cannot stop the old instance.
2. Identify the server that owns the old data. Inspect the backup read-only for the guild ID in `news_settings` or `news_deliveries`. An old `DISCORD_GUILD_ID` or `discord-guild-id.txt` is also a clue. Verify the ID using **Copy Server ID** in Discord with Developer Mode enabled. If ownership is unclear, keep the backup and pause the migration.
3. If `uma-alerts-<guild_id>.sqlite3` is absent, copy the backup under that name into `UMABOT_DATA_DIR` or, for a default local setup, the repository's `data/` folder. Also check the repository root for an existing numbered file. If both the unnumbered and numbered files exist, inspect separate copies of each before deciding whether any data needs to move. Keep the existing numbered database in place.
4. Remove the old `DISCORD_GUILD_ID` setting when upgrading, start the bot, and verify the posting channel with `/uma setup` and subscriptions with `/uma subscriptions`.

If an older local installation stored numbered alert or feedback databases at the repository root, stop the bot and back them up before upgrading. Before starting the upgraded bot, copy them into `data/` or your configured `UMABOT_DATA_DIR` only where the corresponding destination is absent. UmaBot logs a warning when a root-level database has no corresponding file in the active data directory.

## Project layout

- `umabot/` contains the Discord client, startup configuration, alert delivery, and embeds.
- `umabot/commands/` contains the slash commands.
- `umabot/game/` contains the game data clients, shared feed cache, and condition formatting.
- `data/` is the default SQLite directory when `UMABOT_DATA_DIR` is unset. It is created as needed and ignored by Git.

Start UmaBot with `python -m umabot` from your virtual environment.

## Using the bot

UmaBot registers `/uma` in each server it joins. Run `/uma setup` in each server that wants automated posts and pings. Each server has its own posting channel and subscriptions.

Run `/uma ping` to mention yourself in the current channel and measure how long UmaBot takes to reply. Use `/uma help` for the full command list.

Use **Stats** on a support card to see its game data at the level cap for each limit break from **0LB** through **4LB**.

### Command reference

| Command | What it does |
| --- | --- |
| `/uma character` | Look up a character by name or numeric ID. |
| `/uma support`, `/uma cards` | Find a support card by card or character name. |
| `/uma borrow find` | Find Global trainers listing the selected EN support card. Minimum limit break defaults to 0. Set `UMAMOE_API_KEY` in the bot host’s secret environment to show Friend IDs in Discord. |
| `/uma compare` | Pick the first card, its limit break, the second card, then its limit break (defaults to 4LB). Shared stats appear first, followed by stats active on only one card. Select **Card details** for unique effects. |
| `/uma skill`, `/uma unique` | Find a shared skill or a trainee's unique skill. |
| `/uma track` | Show a racetrack and its courses, optionally filtered by distance in meters. |
| `/uma birthdays` | Show today's character birthdays. |
| `/uma cm`, `/uma events` | Check the Champions Meeting schedule, EN events, missions, and banners. |
| `/uma banner` | Show active EN trainee and support card banners, featured picks, and end times. |
| `/uma upcoming` | List EN starts over the next 30 days, with a separate GameTora forecast based on the Japanese release schedule. Forecast dates and lineups can change. |
| `/uma digest` | View or configure the weekly EN schedule digest. Requires **Manage Server**. |
| `/uma subscriptions` | View or remove character, support card, or event announcement subscriptions in this server. |
| `/uma alert`, `/uma reminders` | Set your alert delivery time and event reminder window, or turn deadline pings on or off. |
| `/uma setup`, `/uma news` | Set the shared posting channel. Requires **Manage Server**. |
| `/uma feedback` | Send feedback privately to the bot owner. |
| `/uma help`, `/uma ping` | List commands or measure the bot's reply time. |

## Events and reminders

- `/uma cm` shows the active EN Champions Meeting or the next one. When the EN schedule runs out, it shows a labeled GameTora preview. Search by name to look up a past cup.
- `/uma events` shows active and upcoming EN events, limited missions, and standard banners from GameTora.
- Use `/uma digest channel:#uma-news day:Monday delivery_time:09:00` to post a weekly summary of confirmed starts and deadlines for the next seven days. GameTora forecasts appear separately and may change. Delivery uses the bot host's local time and sends no mentions. Run `/uma digest enabled:False` to turn it off.
- Use `/uma alert` to choose a daily delivery time on the computer running UmaBot or receive alerts as soon as they qualify. Enter a custom time such as `5:30 PM` and choose how soon before an event ends to get a reminder, from 1 to 168 hours. Existing schedules keep the same clock time and follow the bot host's time zone. A scheduled reminder is sent only if the event qualifies at the chosen time, and only once per event.
- Choose **Events** in `/uma subscriptions` to check or remove your subscription to new EN event announcements in this server.
- Use `/uma reminders` to turn event-ending pings on or off. The default window is 24 hours. Server managers can use `/uma news` or `/uma setup` to select the shared posting channel.

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
