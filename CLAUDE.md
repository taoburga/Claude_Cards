# Claude Cards - Project Context

> This file provides context for future Claude instances working on this project.

## GitHub Repository

**Repo:** https://github.com/taoburga/Claude_Cards (private)

**Important:** When making significant changes to the codebase, remember to commit and push updates to the repo. The user may ask you to do this periodically.

```bash
cd /Users/taoburga/Desktop/Claude_Cards
git add -A
git commit -m "Description of changes"
git push
```

## Overview

**Claude Cards** is a macOS app that creates Anki flashcards from screenshots using Claude's vision API. The user takes a screenshot of content they want to learn, and Claude automatically generates a flashcard and adds it to their Anki deck.

## Current State (as of 2026-01-24)

### Working Features
- **Screenshot capture**: Cmd+Shift+1 triggers Automator Quick Action → runs `capture_for_flashcard.sh`
- **File watcher**: `flashcard_watcher.py` monitors `screenshots/` folder using watchdog
- **Claude vision processing**: Sends screenshot to Claude API, gets back flashcard JSON
- **Anki integration**: Uses AnkiConnect API to add cards to "Concepts" deck
- **Selection detection**: Grabs highlighted text from Chrome via JavaScript, tells Claude to focus on it
- **Clipboard fallback**: If no Chrome selection, checks clipboard for recently copied text
- **Clean source attribution**: Shows just "Source: domain.com" (no ugly links). Skips search engines/redirects.
- **Menu bar app**: Shows status, model switcher, card types, usage stats, quick actions
- **Image embedding**: Auto-detects diagrams/charts, embeds in Anki card (configurable: auto/always/never)
- **Preview mode**: Optional dialog to preview/skip card before saving
- **Multiple card types**: Can generate Basic, Cloze, and Reverse cards from same screenshot
- **Duplicate detection**: Warns if similar card exists, option to skip
- **API cost tracking**: Tracks tokens, calculates cost, shows daily/total in menu bar
- **macOS notifications**: Confirms when card is created or skipped
- **Auto-start**: LaunchAgents for both watcher and menu bar app
- **Offline queue**: Cards are queued in `pending_cards.json` when Anki isn't running, processed automatically when Anki starts

### File Structure
```
/Users/taoburga/Desktop/Claude_Cards/
├── CLAUDE.md                  # This file - auto-loads for Claude sessions
├── capture_for_flashcard.sh   # Screenshot capture script (Cmd+Shift+1)
├── config.json                # All configuration options
├── flashcard_watcher.py       # Main Python daemon (watches for screenshots + HTTP server)
├── menubar_app.py             # Menu bar app (status, settings, model switcher)
├── ClaudeCards.app/           # Dock-friendly app for quick capture (drag to Dock!)
├── browser_extension/         # Chrome extension for text selection → flashcard
├── requirements.txt           # Python dependencies
├── screenshots/               # Where screenshots land (watched folder)
├── usage_stats.json           # API usage and cost tracking
├── pending_cards.json         # Queue for cards when Anki is offline
└── *.log                      # Various log files

~/Library/Services/Capture Flashcard Screenshot.workflow  # Automator Quick Action
~/Library/LaunchAgents/com.claudecards.flashcardwatcher.plist  # Watcher auto-start
~/Library/LaunchAgents/com.claudecards.menubar.plist  # Menu bar auto-start
```

### Configuration Options (config.json)

**To edit:** `~/Desktop/Claude_Cards/config.json`

```json
{
  "anthropic_api_key": "...",           // API key (or use ANTHROPIC_API_KEY env var)
  "anki_deck": "Concepts",              // Target Anki deck
  "anki_connect_url": "http://localhost:8765",
  "screenshots_dir": "...",
  "model": "claude-sonnet-4-20250514",  // haiku/sonnet/opus - CHANGE MODEL HERE
  "include_image": "auto",              // auto/always/never
  "preview_before_save": false,         // Show dialog before saving
  "check_duplicates": true,             // Warn about similar cards
  "card_types": ["basic"],              // basic/cloze/reverse
  "prompt": "..."                       // CHANGE SYSTEM PROMPT HERE
}
```

**Available models:**
- `claude-3-5-haiku-20241022` - Fast, cheap (~$0.001/card)
- `claude-sonnet-4-20250514` - Balanced (default)
- `claude-opus-4-20250514` - Best quality, most expensive

### Dependencies
- Python 3.12 (system)
- watchdog, anthropic, requests, rumps (pip installed)
- AnkiConnect add-on in Anki (code: 2055492159)
- Anki must be running for cards to be added

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Cmd+Shift+1    │────▶│  screenshots/    │────▶│  flashcard_watcher  │
│  (Automator)    │     │  folder          │     │  (watchdog)         │
└─────────────────┘     └──────────────────┘     └──────────┬──────────┘
                                                            │
    ┌───────────────────────────────────────────────────────┤
    │                                                       │
    ▼                                                       ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ Chrome      │   │ Anthropic   │   │ Duplicate   │   │ AnkiConnect │
│ Selection   │   │ Vision API  │   │ Check       │   │ API         │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
                        │
                        ▼
                  ┌─────────────┐
                  │ Track Usage │
                  │ (costs)     │
                  └─────────────┘
```

## Key Functions

### flashcard_watcher.py
- `get_chrome_context()` - Gets URL, title, and selected text from Chrome via AppleScript
- `get_clipboard_text()` - Fallback for selected text
- `clean_url()` - Strips tracking parameters
- `get_page_metadata()` - Extracts Open Graph metadata via JavaScript
- `create_flashcard_from_image()` - Sends to Claude API, returns flashcard dict
- `check_for_duplicates()` - Searches Anki for similar cards
- `add_to_anki()` - Adds card(s) via AnkiConnect, or queues if Anki offline
- `add_to_queue()` - Saves card to pending_cards.json for later
- `process_pending_queue()` - Processes queued cards when Anki becomes available
- `queue_processor_loop()` - Background thread checking queue every 30 seconds
- `track_usage()` - Logs API token usage and costs
- `ScreenshotHandler` - Watchdog event handler for new screenshots

### menubar_app.py
- Shows watcher/Anki status
- Model switcher (Haiku/Sonnet/Opus)
- Image inclusion setting (auto/always/never)
- Card types toggle (basic/cloze/reverse)
- Preview before save toggle
- Duplicate check toggle
- Usage stats (today/total cards and cost)
- Start/stop watcher
- Open screenshots folder, config, logs

## Future Plans

### Completed
- [x] Browser extension for one-click capture
- [x] GitHub repository setup (initialized, ready to push)

### Ideas for Future
- Readwise integration
- Obsidian/Notion sync
- iOS Shortcut for mobile
- Voice note attachment
- Weekly digest email
- Spaced repetition optimization hints

## Troubleshooting

### Hotkey not working (Cmd+Shift+1)
This is the most common issue. The Automator Quick Action shortcut can be flaky on macOS.

**Step-by-step fix:**
1. Run in terminal: `/System/Library/CoreServices/pbs -update`
2. Open **System Settings → Keyboard → Keyboard Shortcuts → Services → General**
3. Find "Capture Flashcard Screenshot"
4. If shortcut shows, click it and **re-press Cmd+Shift+1** to rebind
5. If not listed or greyed out, uncheck then re-check the box
6. If still not working, **logout and login** (full logout, not just lock)
7. If STILL not working, the workflow may need recreation:
   ```bash
   # Remove old workflow
   rm -rf ~/Library/Services/Capture\ Flashcard\ Screenshot.workflow
   # Then recreate via Automator (see README.md for steps)
   ```

**Alternative options:**

1. **Dock app**: Drag `ClaudeCards.app` to your Dock. Click it to capture a screenshot.
   - Find it at: `~/Desktop/Claude_Cards/ClaudeCards.app`
   - Or open Finder → Go → Go to Folder → paste the path

2. **Spotlight**: Press Cmd+Space, type "ClaudeCards", press Enter

3. **Browser extension**: Select text in Chrome → right-click → "Create Flashcard"
   - Install from `chrome://extensions/` → Load unpacked → select `browser_extension/` folder

4. **Terminal** (last resort):
   ```bash
   ~/Desktop/Claude_Cards/capture_for_flashcard.sh
   ```

### Watcher not processing
1. Check menu bar icon (📚 = running, 📚💤 = stopped)
2. Check logs: `tail -f ~/Desktop/Claude_Cards/flashcard_watcher.log`
3. Restart via menu bar: Stop Watcher → Start Watcher

### Cards not appearing in Anki
1. Verify Anki is running
2. Check AnkiConnect: http://localhost:8765 in browser
3. Verify deck name in config.json matches Anki

### Menu bar app not showing
1. Run manually: `python3 ~/Desktop/Claude_Cards/menubar_app.py`
2. Check for errors in menubar_stderr.log

## Session History

### 2026-01-24: Initial Build + All Enhancements Complete
- Created full pipeline: screenshot → Claude → Anki
- Set up Automator Quick Action with Cmd+Shift+1
- Configured LaunchAgent for auto-start
- Added Chrome selection detection via JavaScript
- Added clipboard fallback for non-Chrome screenshots
- Added URL cleaning (tracking param removal)
- Added page metadata extraction (author, date, site)
- Created menu bar app with rumps
- Added image embedding option (auto/always/never)
- Added preview dialog before saving
- Added multiple card types (basic/cloze/reverse)
- Added duplicate detection and warning
- Added API cost tracking with daily/total stats
- Created Chrome browser extension with HTTP server backend (port 8766)
- Added `create_flashcard_from_text()` for extension requests (no image needed)
- Set up GitHub repository (README, LICENSE, .gitignore, config.example.json)
- Updated workflow to use full path instead of ~ (tilde doesn't expand in Automator)

### Known Issues
- **Keyboard shortcut (Cmd+Shift+1)**: macOS Automator shortcuts can be unreliable. Common fixes:
  - Workflow must be configured to receive "no input" (not text or files)
  - Input method should be "as arguments" not "to stdin"
  - May need to re-bind in System Settings → Keyboard → Shortcuts after updates
  - See Troubleshooting section for step-by-step fixes
