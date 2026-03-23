# Claude Cards - Project Context

> This file provides context for AI agents (Claude, Cursor, etc.) working on this project.

## Overview

**Claude Cards** is a macOS app that creates Anki flashcards from screenshots using Claude's vision API. Take a screenshot of content you want to learn, and Claude automatically generates a flashcard and adds it to your Anki deck.

## Architecture

```
Screenshot (Cmd+Shift+1)  ──▶  screenshots/ folder  ──▶  flashcard_watcher.py (watchdog)
                                                              │
Browser Extension  ──▶  HTTP server (port 8766)  ─────────────┤
                                                              │
                    ┌─────────────────────────────────────────┤
                    │                  │                       │
                    ▼                  ▼                       ▼
             Chrome context     Claude Vision API       AnkiConnect API
             (URL, title,       (tool_use for           (localhost:8765)
              selection)         structured output)           │
                                       │                      ▼
                                       ▼               Anki deck or
                                  Cost tracking        offline queue
```

### Key design decisions
- **Claude tool_use** for structured flashcard output (not JSON-in-prompt)
- **macOS Keychain** for API key storage (fallback: env var, then config.json)
- **Event-driven file watching** via watchdog/FSEvents (not polling)
- **Edge-triggered queue**: only delivers queued cards on Anki launch detection, sleeps when queue is empty
- **Thread-safe** duplicate event guard (watchdog can fire multiple events per file)
- **Web dashboard** at `localhost:8766` for config editing and usage stats (no extra dependencies)
- **Config hot-reload**: dashboard changes take effect without restarting the watcher

## File Structure

```
Claude_Cards/
├── flashcard_watcher.py       # Main daemon: file watcher + Claude API + AnkiConnect + HTTP server + dashboard
├── capture_for_flashcard.sh   # Screenshot capture script (called by Automator)
├── config.example.json        # Template config (copy to config.json)
├── requirements.txt           # Python deps: watchdog, anthropic, requests
├── browser_extension/         # Chrome extension for text selection → flashcard
│   ├── manifest.json
│   ├── background.js
│   ├── popup.html / popup.js
│   └── icons/
├── ClaudeCards.app/           # macOS app bundle — opens web dashboard
├── launchagents/              # Template LaunchAgent plist for auto-start
│   └── com.claudecards.flashcardwatcher.plist
├── CLAUDE.md                  # This file
├── README.md                  # User-facing documentation
├── LICENSE                    # MIT
└── .gitignore
```

**Not tracked** (generated at runtime):
- `config.json` - user's config with API key reference
- `screenshots/` - captured screenshots
- `usage_stats.json` - API cost tracking data
- `pending_cards.json` - offline card queue
- `watcher.pid` - PID file for process management
- `*.log` - log files (rotating, 5MB max, 3 backups)

## Key Functions (flashcard_watcher.py)

| Function | Purpose |
|----------|---------|
| `get_api_key_from_keychain()` | Retrieves API key from macOS Keychain |
| `reload_config()` | Hot-reloads config.json, preserving runtime API key |
| `get_chrome_context()` | Gets URL, title, selected text from Chrome via AppleScript |
| `create_flashcard_from_image()` | Sends screenshot to Claude API (tool_use), returns flashcard |
| `create_flashcard_from_text()` | Creates flashcard from text (browser extension path) |
| `_call_with_retry()` | Exponential backoff wrapper for API calls |
| `_extract_flashcard_from_response()` | Extracts structured data from tool_use response |
| `_sanitize_for_applescript()` | Prevents AppleScript injection in user content |
| `check_for_duplicates()` | Searches Anki for similar existing cards |
| `add_to_anki()` | Adds cards via AnkiConnect, queues if Anki offline |
| `process_pending_queue()` | Delivers queued cards when Anki becomes available |
| `anki_launch_watcher()` | Edge-triggered background thread for queue delivery |
| `ScreenshotHandler` | Watchdog event handler with thread-safe dedup |

## Configuration (config.json)

Copy `config.example.json` to `config.json` and configure:

| Key | Description | Default |
|-----|-------------|---------|
| `anthropic_api_key` | API key (prefer Keychain instead) | `""` |
| `anki_deck` | Target Anki deck name | `"Concepts"` |
| `model` | Claude model | `"claude-sonnet-4-6"` |
| `include_image` | Embed screenshots: auto/always/never | `"auto"` |
| `card_types` | Array of: basic, cloze, reverse | `["basic"]` |
| `preview_before_save` | Show preview dialog | `false` |
| `check_duplicates` | Warn on similar cards | `true` |
| `prompt` | System prompt for flashcard generation | (see example) |

### API key setup (recommended)
```bash
security add-generic-password -a ClaudeCards -s ClaudeCards -w 'sk-ant-...' -U
```

## Security notes
- CORS restricted to `chrome-extension://` origins only
- HTTP server has 1MB request size limit
- AppleScript inputs are sanitized against injection
- PID file used for process management (not `pkill -f`)
- API errors return generic messages (no internal details leaked)
- Log rotation prevents unbounded disk usage

## Dashboard

The watcher serves a web dashboard at `http://localhost:8766` for:
- Viewing usage stats (cards created, API costs, today's activity)
- Editing settings (model, deck, card types, image inclusion, duplicates, preview)
- Editing the system prompt

The `ClaudeCards.app` opens this dashboard in the default browser.

API endpoints:
- `GET /` — Dashboard HTML
- `GET /api/config` — Current config (API key excluded)
- `POST /api/config` — Update config (whitelisted fields only)
- `GET /api/usage` — Usage statistics

## Dependencies
- Python 3.10+
- macOS (uses AppleScript, FSEvents, Keychain, Automator)
- Anki with AnkiConnect add-on (code: 2055492159)
- pip packages: `watchdog`, `anthropic`, `requests`
