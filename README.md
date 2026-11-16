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
| One place for DeepSeek, GPT, Copilot, Grok, Gemini, Claude and others | Adapters for the Anthropic API, every OpenAI-compatible API (OpenAI, DeepSeek, xAI, Gemini, GitHub Models, OpenRouter, Mistral, Ollama), and the official **subscription CLIs** (Claude Code, Codex, Copilot CLI, Gemini CLI), so tasks can spend your *plans*, not just API credit |
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

## Install

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

Each installer creates a virtualenv, writes the config files, and registers a background service
(Task Scheduler at logon on Windows, launchd on macOS, systemd on Linux/Pi). Check any machine with
`switchboard doctor`.

Config lives in `%APPDATA%\Switchboard` (Windows), `~/Library/Application Support/Switchboard` (macOS)
or `~/.config/switchboard` (Linux/Pi):

| File | What |
|---|---|
| `config.yaml` | node name/role, hub URL, providers, router settings, permissions, MCP servers, devices, servers, notifications |
| `secrets.env` | API keys and the shared token (hub needs the API keys; agents only need the token) |
| `credits.yaml` | your credits, free tiers, subscriptions, budgets |
| `models.yaml` | your catalog overrides (written by `models sync`) |

## Use it

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
- **Subscription quotas** (ChatGPT Plus/Pro, Claude Pro/Max, Copilot, Google AI plans) have no API to
  read what is left. Switchboard uses them through the official CLIs and counts requests against the
  quota you enter. Log in to each CLI once on the hub (`claude`, `codex`, `copilot`, `gemini`).
  SuperGrok has no CLI, so Grok runs on xAI API credit.
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
