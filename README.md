# Switchboard

One control plane for all your AI models, credits, tools, devices and machines.
Runs on **Windows, macOS and Raspberry Pi**.

You write a prompt. Switchboard either uses the model and settings you pick (for example
`gpt-6` at `max` effort) or works out the best ones itself, spends the right credit, uses tools on
whichever machine has them (Unity, Photoshop, Blender, phones, servers) and saves the result to
your output folder on every machine.

```
                        ┌──────────────────────── HUB (Raspberry Pi, always on) ───────────────────────┐
  you ─ dashboard ─────►│ router ─ credit ledger ─ scheduler/alerts ─ provider adapters ─ job queue     │──► Claude, GPT, Gemini,
  you ─ CLI ───────────►│   │            │                │                    │             ▲           │    Grok, DeepSeek, GitHub
  Claude Desktop/VS Code│   │            │                └─ ntfy push to phone │             │           │    Models, Mistral, Ollama
  Cursor/Codex (MCP) ──►│   └─ output folder                                  │             │ long-poll │
                        └─────────────────────────────────────────────────────┼─────────────┼───────────┘
                                         subscription CLIs (Claude Code,       │             │
                                         Codex, Copilot, Gemini CLI)            ▼             │
                ┌──────── AGENT: Windows PC ───────┐          ┌──────── AGENT: Mac ────────────┐
                │ MCP: Unity, Photoshop, Blender   │          │ MCP: Photoshop, mobile, Xcode  │
                │ adb → Android phone              │          │ iOS devices & simulators       │
                │ output mirror ~/Switchboard/Output│         │ output mirror ~/Switchboard/Output
                └──────────────────────────────────┘          └────────────────────────────────┘
```

## What it does

| You asked for | How Switchboard does it |
|---|---|
| One place for DeepSeek, GPT, Copilot, Grok, Gemini, Claude and others | Adapters for the Anthropic API, every OpenAI-compatible API (OpenAI, DeepSeek, xAI, Gemini, GitHub Models, OpenRouter, Mistral, Ollama), and the official **plan CLIs** (Claude Code, Codex, Gemini CLI, Copilot CLI, Grok Build) |
| Use the chat apps' daily limits instead of credits | `plans_only` / `plans_first` billing runs tasks through those plan CLIs (signed in with your subscriptions) on whichever machine they're installed on, with your tools attached. When a plan hits its limit it is parked until it resets and the next plan takes over (see below) |
| Choose settings yourself, or let it optimise | Manual: provider, model, effort (`low`…`max`). Auto: classifies the task (category + complexity 1-5) and scores every funded model on quality vs. effective cost, in `balanced`, `best` or `cheapest` mode |
| "If max is too much for a task, reduce it" | Picks the effort from the task's complexity. If you choose `max` for a trivial task it warns you and shows the cheaper estimate (CLI asks; set `auto_downgrade_effort: true` to lower it automatically) |
| Know my credits and use them properly | Credit ledger with prepaid grants (with expiry), free tiers (with reset schedules), subscriptions and pay-as-you-go budgets. It reads live balances where the provider exposes one (DeepSeek, OpenRouter) and logs every call's tokens and cost |
| "If a credit expires tomorrow, use it immediately" | Expiring credit gets an urgency score that makes it nearly free in the router, so suitable tasks go there first, and you get a critical alert |
| "Tell me to use a free limit before it resets" | Alerts when most of a free or plan quota is unused shortly before it resets (3 h before for daily, 24 h for weekly, 72 h for monthly). Shown on the dashboard and pushed to your phone via ntfy |
| Use Unity, VS Code, Photoshop… | Every machine runs its own MCP servers (catalog: Unity, Photoshop, Blender, filesystem, Playwright, GitHub, mobile, Hostinger, desktop). The hub offers them to the model and runs each tool call on the machine that has it |
| Suggest a better tool, installable on Mac/Windows/Pi | Tool advisor: per-task suggestions plus a catalog with the right `winget` / `brew` / `apt` command per machine. One click (with confirmation) installs it on that machine |
| Dedicated output folder on Windows and Mac | `~/Switchboard/Output/<date>/<time>-<task>/` with `prompt.md`, `response.md`, extracted code files, images and `meta.json`. The hub copies every task folder to each agent |
| Test on phones, PS4, servers like Hostinger | Android (adb: screenshot, logs, install, launch, open URL), iOS devices and simulators (Xcode tools on the Mac), SSH servers (check, deploy with rsync/scp, run), and reachability checks for network devices. See limits below |
| Use it from other AI apps | `switchboard mcp serve` makes Switchboard an MCP server. `switchboard mcp export` registers it with Claude Desktop, Claude Code, VS Code, Cursor, Codex or Gemini CLI |

## Using it: the app

After installing, everything happens in the **Switchboard app**, a dashboard you open in any browser that
you can also install like a normal app. No terminal needed.

| Where | How |
|---|---|
| **Your Windows PC or Mac** (with the agent) | Click the **Switchboard icon** in the system tray / menu bar, then **Open Switchboard**. It opens `http://localhost:8766`, which needs no token. Use the browser's **Install** button (Chrome/Edge address bar) to get it as its own app window with an icon |
| **The hub itself** | `http://localhost:8765` |
| **Phone, tablet, any other device at home** | `http://<pi-ip>:8765` (the Pi installer prints it). Enter the token once (*Settings → About* shows it). On iPhone: Share → *Add to Home Screen*; on Android: menu → *Install app / Add to Home screen* |
| **Away from home** | Install [Tailscale](https://tailscale.com) on the Pi and your phone/laptop, then open the Pi's Tailscale address. Never port-forward 8765 on your router |
| **Inside your AI apps** | `switchboard mcp export --target claude-desktop --write` (also `vscode`, `cursor`, `claude-code`, `codex`, `gemini-cli`), then chat in that app as usual |

What's in the app:

- **Ask**: a chat. Type a task and Switchboard picks the AI (or tap **Automatic** to choose one yourself),
  shows "will use Claude, your plan, no credit" before you send, and asks before anything expensive or
  when you picked more effort than the task needs. Follow-up questions continue the conversation.
  Answers show code with copy buttons, images, and downloadable files.
- **History**: every task with its answer and files; search it, ask again, or continue a chat.
- **Plans & credits**: each plan's status ("Ready on win-pc", "At its limit, resets 15:00"), with
  **Set up** (installs the plan app on the computer you choose) and **Sign in** buttons. Also your credits,
  free limits and budgets with progress bars and expiry dates, an **Add** wizard, and spending this month.
- **Machines & apps**: your computers and their phones, consoles and servers (screenshots, logs, SSH
  checks). **Connected apps** adds Unity, Photoshop, Blender, browser testing, phones, GitHub, Hostinger and
  more to any computer, with setup steps shown where needed. **Install apps** gives one-click installs per
  computer. **Connect a computer** shows the exact command for Windows, Mac or Pi with your token filled in.
- **Settings**: API keys (paste, saved only on the hub, never shown again), default spending,
  quality vs cost, what AI may do on its own, phone alerts (with a test button), checking for new models
  and prices, and light/dark appearance.

The **tray / menu-bar icon** (Windows, Mac) turns green when all is good, amber or red when credit is
about to be lost or a plan hits its limit, and grey when the hub can't be reached. It shows a desktop
notification for each new alert, and has shortcuts to the app and the output folder. On a PC or Mac it
also runs the agent, so it is the only thing that needs to run there.

## Running on your plans' daily limits (no API credit)

Switchboard can do all its work through your **subscriptions**, the same daily/weekly limits you use in
the chat apps, instead of paid API credit. It does this through each company's official command-line app,
which signs in with your plan account:

| Plan | Official CLI | Sign in once (on any of your machines) |
|---|---|---|
| Claude Pro / Max | Claude Code (`claude`) | `switchboard tools install claude_code`, then run `claude` and log in |
| ChatGPT Plus / Pro | Codex (`codex`) | `switchboard tools install codex`, then `codex login` |
| Google account / Google AI plan | Gemini CLI (`gemini`) | `switchboard tools install gemini_cli`, then run `gemini` and sign in with Google |
| GitHub Copilot (Free / Pro) | Copilot CLI (`copilot`) | `switchboard tools install copilot_cli`, then run `copilot` and `/login` |
| SuperGrok / X Premium+ | Grok Build (`grok`, beta) | install from xAI's docs, then `grok auth login` |

Easiest: in the app, open **Plans & credits**, click **Set up** on a plan (it installs the plan app on the
computer you pick), then **Sign in** (Claude, ChatGPT, Copilot and Grok open their sign-in on that
computer; Gemini asks you to run `gemini` once there). The commands above do the same from a terminal.
Install them on whichever machine you like (the PC and Mac where you are already logged in are fine).
The hub runs each one **on the machine where it is installed**. Your MCP tools (Unity, Photoshop,
phones...) are passed to them through Switchboard's tool gateway, and files they create come back to
the hub and every output folder.

**Automatic images:** choose **Automatic** or **Codex images**. A connected Codex installation must
advertise its native `image_generation` feature. Switchboard generates the image in that machine's
task folder, checks that an actual PNG/JPEG/WebP was produced, returns it to the hub and mirrors it.
Choosing the normal Codex route for an image request redirects to this image route when available.
If generation returns only text, the task fails with a clear message rather than claiming an image exists.

**Manual app handoffs:** ChatGPT/Grok app routes open the app and copy a prompt; they do not submit it
or generate an image automatically. They are grouped separately in the model picker, require explicit
confirmation, and show **Action needed from you** while waiting. Automatic routes never silently fail
over into a manual handoff. Subscription quota errors (including Claude weekly limits) park the plan
until its reported reset time; automatic routing can then use another funded provider. An effort
preference alone does not disable provider failover.

Billing modes (`router.billing` in `config.yaml`, the dashboard's billing menu, or `--plans-only` / `--billing`):

- `plans_only`: never spends API credit. It uses plan CLIs, free tiers (Gemini free API, GitHub Models)
  and local models only. When every plan has hit its limit, it tells you which resets first and when.
- `plans_first` (default): plans and free tiers first; credit only when no plan can do the task
  (for example image generation) or all plans are at their limit.
- `any`: picks purely on quality vs. cost.

When a CLI reports that its limit is reached, Switchboard reads the reset time from the message, parks
that plan until then (the dashboard's Plans panel shows "limit reached · resets 15:00"), and moves on
to the next plan. Set `permissions.cli_agents_can_edit: true` to let these CLIs write files in the
task folder.

**Images on your ChatGPT or SuperGrok plan.** The plan CLIs can't make images, so image requests are
handed to the ChatGPT or Grok app instead (both enabled by default; set `enabled: false` under
`providers:` for a plan you don't have). The task shows **Open ChatGPT on win-pc** (or Grok): Switchboard
opens the desktop app on that computer, or the website if the app isn't installed, with the prompt on
the clipboard. Paste it, make the image, and save it to that computer's Downloads folder. The agent
notices the new image and attaches it to the task, with a link in the dashboard and a copy in your output
folders. You can also add any file to a task with **Add a file**. In `any` mode, a paid image model is
used instead, since that needs no manual step.

**Apps continue each other's work.** Each plan app (Claude Code, Codex, Gemini, Copilot) is told where
the last few tasks saved their files and can read them. Claude Code, Gemini and Copilot get read access
to the output folder. So "now add tests to that script" works even if a different app wrote the script.
Turn it off with `router.share_recent_work: false`.

**Why not drive the chat websites directly?** claude.ai, chatgpt.com, gemini.google.com, grok.com and
copilot.microsoft.com don't allow scripted use. Automating them breaks their terms and gets accounts
banned, and it stops working whenever they change their pages. The CLIs above are the official way to use
the same plans from other software. Chat-only plans with no CLI (for example DeepSeek's free chat) can't
be used this way; DeepSeek's API is very cheap instead.

## Install

Download the app for each computer from the [releases page](https://github.com/baguixgroup/switchboard/releases)
(Python is included; optional tools are added when you need them):

| Computer | File | Install |
|---|---|---|
| **Mac** (Apple Silicon or Intel) | `Switchboard-<version>-macos-arm64.dmg` / `-x86_64.dmg` | Open the DMG, drag Switchboard to Applications. First time: right-click it, **Open** (it isn't from the App Store) |
| **Windows** 10/11 | `Switchboard-<version>-Setup.exe` | Run it (no admin needed). If SmartScreen appears: **More info**, **Run anyway** |
| **Raspberry Pi** (64-bit) / Debian / Ubuntu | `switchboard_<version>_arm64.deb` (`_amd64` for PCs) | `sudo apt install ./switchboard_<version>_arm64.deb` |

**Then open Switchboard.** The setup wizard asks one question: is this computer the **hub** or does it
**connect to your hub**?

1. **Set up the hub first**, on the computer that stays on (a Raspberry Pi is ideal). On a Pi without a screen,
   run `switchboard setup` over SSH (it asks the same questions in the terminal), or `switchboard setup --web`
   to finish it from your phone's browser.
2. **On every other computer**, open Switchboard and choose **Connect to my hub**. It finds the hub on your
   network by itself and asks for a **pairing code**: the hub's dashboard shows one under
   **Machines > Connect a computer** (6 digits, works once, 10 minutes). No long token to copy.

That's it: Switchboard starts at login (tray / menu-bar icon on Windows and Mac, a background service on the
Pi). To change a computer's role later, use **Change setup...** in the tray menu, or run `switchboard setup`.
The hub can also hand out the installers itself: put them in its config folder under `downloads/` and the
**Connect a computer** dialog links to them.

Under **Machines**, **Desktop apps** lists installed Claude and ChatGPT apps; **Plan CLIs** lists
Claude Code, Codex and other command-line clients used for automatic subscription tasks. Installing a
desktop app does not automatically install its command-line client or sign in to it.

The background app finds commands in standard user installation locations, including nvm, Homebrew,
`~/.local/bin`, Windows user/system PATH entries and npm's user bin folder. It detects Windows Store
Claude/ChatGPT registrations and Mac apps in `/Applications` or `~/Applications`. You do not need to
launch Switchboard from a terminal for it to find these tools.

When installing an npm-based client, Switchboard checks for **node, npm and npx** first. If any is
missing, the confirmation includes installing official Node.js LTS binaries, verified against Node's
SHA-256 checksums, into a per-user runtime folder. It does not require Homebrew or admin access.
Use **Machines → Rescan** to refresh detection after installing an app yourself.

Command line on each computer: `switchboard` (Pi/Linux), `switchboard-cli` (Windows),
`/Applications/Switchboard.app/Contents/MacOS/Switchboard` (Mac). Non-interactive setup:
`switchboard setup --role agent --hub 192.168.1.50 --code 123456`.

### Building the installers

Push a tag (`git tag v0.3.0 && git push --tags`) and GitHub Actions builds all of them
(`.github/workflows/release.yml`) and attaches them to a release. To build one on its own platform:
`bash packaging/macos/build.sh`, `powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1`
(needs Inno Setup 6), or `bash packaging/linux/build-deb.sh` (build on the oldest Pi OS you support).
The builds are unsigned: see the first-open notes above. Set `DEVELOPER_ID` for `build.sh` to sign the Mac app.

### From source (developers)

You need Python 3.10+ on each machine. Get this folder onto each machine (`git clone` of your repo, or unzip it).

**1. Hub (Raspberry Pi; any machine works)**

```bash
./scripts/install-pi.sh hub
```

It prints the dashboard address and the **token** agents need. Then add your API keys:

```bash
nano ~/.config/switchboard/secrets.env
```

**2. Windows PC (agent).** In PowerShell, from this folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Role agent -HubUrl http://<pi-ip>:8765 -Token <token>
```

**3. Mac (agent)**

```bash
./scripts/install-macos.sh agent http://<pi-ip>:8765 <token>
```

Each installer creates a virtualenv, writes the config files, and sets Switchboard to start at login:
the tray app on Windows (Task Scheduler) and macOS (launchd), and a background service on Linux/Pi
(systemd). After that, use the app. Adding API keys, plans, credits, apps and computers can all be done
there. `switchboard doctor` checks a machine from the terminal if something seems wrong.

Config lives in `%APPDATA%\Switchboard` (Windows), `~/Library/Application Support/Switchboard` (macOS)
or `~/.config/switchboard` (Linux/Pi):

| File | What |
|---|---|
| `config.yaml` | node name/role, hub URL, providers, router settings, permissions, MCP servers, devices, servers, notifications |
| `secrets.env` | API keys and the shared token (hub needs the API keys; agents only need the token) |
| `credits.yaml` | your credits, free tiers, subscriptions, budgets |
| `models.yaml` | your catalog overrides (written by `models sync`) |

## Writing assistant and writing services

In chat, click **Writing** to proofread, improve clarity, paraphrase, shorten, summarize or translate.
Choose a tone and language, then prepare and review the request before sending it. Built-in writing
uses your connected AI models and the chat's spending settings; it requires a configured provider.

The same dialog offers **Grammarly, QuillBot, ProWritingAid and LanguageTool** through your own accounts.
These are explicitly labelled **manual handoffs**, not API connections: send the prepared request,
open the service on an online Mac/Windows agent with the source text copied, paste and edit there,
then click **Paste edited text** in Switchboard to finish the task. You can also copy the text and open
the service's website yourself. Apply tone/translation choices in the service where supported.
Features, subscriptions and quotas are managed by each service; Switchboard does not read passwords,
check account balances or automate their websites. These services are never chosen by automatic routing.
The reviewed result is saved in the task's `response.md` and mirrored like other task output.

Grammarly's former editor SDK was [retired in January 2024](https://www.grammarly.com/blog/company/general-availability-grammarly-text-editor-sdk/).
LanguageTool's [public HTTP API rules](https://dev.languagetool.org/public-http-api.html) prohibit automated
requests, so Switchboard uses its editor instead of sending background proofreading requests there.

## Command line (optional)

Everything below can also be done in the app; the commands are there if you prefer a terminal.

Dashboard: `http://<hub>:8765` (on another device it asks for the token once).

```bash
switchboard ask "write a python script that renames my photos by date taken"
switchboard ask -m gpt-6 -e max "design the save system for my unity game"   # manual
switchboard ask --mode cheapest -f notes.txt "summarise this"
switchboard ask -i screenshot.png "why does this layout break on mobile?"
switchboard plan "refactor my api"            # preview model, effort, cost, credit, tools
```

Credits:

```bash
switchboard credits add --id openai-promo --provider openai --type prepaid --amount 18 --expires 2026-12-31
switchboard credits add --id gemini-free --provider gemini --type free_tier --amount 250 --unit requests --reset day --tz America/Los_Angeles --models "gemini-*flash*"
switchboard credits add --id copilot --provider copilot-cli --type subscription --amount 300 --unit premium_requests --reset month
switchboard credits add --id deepseek --provider deepseek --type prepaid --auto-balance
switchboard credits add --id claude-budget --provider anthropic --type payg --budget 30
switchboard credits                            # balances, quotas, alerts
switchboard alerts
```

Push alerts to your phone: install the **ntfy** app, subscribe to a hard-to-guess topic, and set
`notify.ntfy_topic` in the hub's `config.yaml`.

Models:

```bash
switchboard models                 # what you can use now
switchboard models sync            # compare the catalog with what your keys can see, plus current prices
switchboard models sync --write    # save: new models added, unavailable ones disabled, prices updated
```

Tools and MCP servers (run on the machine that has the app):

```bash
switchboard mcp catalog
switchboard mcp add blender        # then restart the agent
switchboard mcp add unity          # prints the one-time Unity package setup first
switchboard mcp list               # start them and list their tools
switchboard tools list             # what's installed here
switchboard tools advise           # what's missing where, with install commands
switchboard tools install adb --node windows-pc
```

Remote app installation can also be requested in chat, for example “Install ChatGPT on Windows.”
Switchboard checks the connected machine and shows an installation confirmation button with the
answer. Microsoft Store terms need separate explicit acceptance. Installations run as tracked jobs
in **History**, survive closing the dashboard, and refresh the machine's detected apps when finished.
A successful installer exit alone is not enough: the target agent must detect the installed app.
If the hub restarts during installation, rescan the target before retrying.

ChatGPT and Claude desktop have executable installers for Windows and macOS. Windows uses the
official winget/Store entries (ChatGPT also retries with OpenAI's signed MSIX when the Store
reports success without registering the app); macOS downloads and verifies the official app before copying it to
`~/Applications`. Unsupported platforms and missing prerequisites are shown as actionable errors.
ChatGPT on Windows installs a packaged service and needs administrator approval. The normal-user
agent reports this requirement and retains the signed MSIX with the command an administrator can
run on that PC; it does not approve Windows elevation prompts.
Installing a desktop app does not sign into it or enable its separate command-line AI provider.

Devices and servers:

```bash
switchboard nodes --refresh
switchboard device screenshot android:R58M12345 --node windows-pc
switchboard device install android:R58M12345 --node windows-pc --arg path=C:/builds/game.apk
switchboard device check server:hostinger
switchboard device deploy server:hostinger --arg path=~/Switchboard/Output/2026-11-16/site
```

Use Switchboard from other apps:

```bash
switchboard mcp export --target claude-desktop --write
switchboard mcp export --target vscode --write      # writes .vscode/mcp.json in the current folder
switchboard mcp export --target claude-code         # prints the `claude mcp add` command
```

## How the router decides

1. **Classify.** Keywords (or the cheapest funded model, with `router.classifier: llm`) give a
   category (code, game_dev, writing, research, reasoning, data, devops, image_gen, image_edit,
   design_3d, mobile_test, chat) and a complexity from 1 to 5.
2. **Filter.** It keeps only models from providers you can use that support what the task needs:
   vision, tools, image generation, long context.
3. **Fund.** For each model it picks the best credit source: free quota first, then expiring prepaid
   credit, then other prepaid, then pay-as-you-go within budget.
4. **Score.** Quality fit for the category and complexity (overkill is penalised) minus effective
   cost (log scale), plus a bonus for credit about to be lost, plus your `router.prefer` bonuses.
5. **Effort** follows complexity: 1-2 low, 3 medium, 4 high, 5 xhigh. It is mapped to each provider's
   native levels (OpenAI `reasoning_effort`, Claude `output_config.effort`, Codex
   `model_reasoning_effort`).
6. **Run with failover.** On a rate limit, an exhausted credit or an outage, it retries the next
   alternative. Tasks above `confirm_above_usd` ask first.

## Limits you should know (honest list)

- **Balances.** DeepSeek and OpenRouter expose live balances. OpenAI, Anthropic, xAI and Google do
  **not** expose prepaid balances or promo-credit expiry through their APIs. Enter those grants once
  (`credits add`); Switchboard then tracks spend from token counts × catalog prices. Keep prices
  current with `models sync`.
- **Plan limits** (ChatGPT, Claude, Copilot, Google, SuperGrok) can't be read in advance: no plan
  exposes "what's left". Switchboard learns a limit when a CLI reports it, then waits for the reset time
  it gives. Optionally enter quotas in `credits.yaml` to get "use it before it resets" reminders.
- **Plan CLIs** change their flags from time to time. The defaults were checked against Claude Code 2.1,
  Codex 0.169, Gemini CLI 0.68 and Copilot CLI 1.0. Grok Build's flags are a best guess (check
  `grok --help`). Override per machine under `providers:` in that machine's `config.yaml`.
- **Image generation** isn't available through the plan CLIs, so it needs an OpenAI or xAI key
  (in `plans_only` mode you get a written image prompt instead).
- **"Copilot"** means GitHub Models (free daily allowance with a GitHub token, OpenAI-compatible API)
  plus the Copilot CLI for your Copilot plan.
- **Model catalog.** Claude entries come from Anthropic's current model table. Other providers'
  entries are marked as unverified seeds (line-ups change monthly): run `models sync --write` with
  your keys before relying on cost estimates.
- **Unity and Photoshop** are reached through community MCP bridges that need a one-time plugin
  setup inside the app (`switchboard mcp add unity` / `photoshop` prints the steps). Neither app runs
  on a Raspberry Pi.
- **PS4.** A retail PS4 cannot sideload or run your builds without a Sony dev kit, so Switchboard only
  checks whether it is reachable. For web builds, open the URL in the PS4 browser yourself.
- **iOS devices** need the Mac with Xcode, a paired device and signing set up.
- **Hostinger.** Deploys use SSH (`servers:` in config; shared hosting uses port 65002). The
  Hostinger MCP server (needs an hPanel API token) covers VPS, DNS and domains.
- **Security.** Keep the hub on your LAN, or use a VPN such as Tailscale. Never expose port 8765 to
  the internet. Remote calls need the token. By default, models may only take read-only device and
  server actions and cannot install software; change this under `permissions:`.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

The tests are offline: they use the `mock` provider and a tiny MCP server
(`tests/fixtures/echo_mcp.py`), and run a real hub, agent and MCP tool round-trip.
