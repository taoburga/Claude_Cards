# Claude Cards

Create Anki flashcards from screenshots or selected text using Claude AI.

## Features

- **Screenshot to Flashcard**: Press `Cmd+Shift+1` to capture any part of your screen. Claude analyzes the image and creates a flashcard automatically.
- **Selection Detection**: Highlight text in Chrome before screenshotting, and Claude focuses on what you selected.
- **Browser Extension**: Right-click selected text in Chrome, "Create Flashcard" (no screenshot needed).
- **Smart Source Attribution**: Automatically captures the URL, cleans tracking parameters, and adds source to your cards.
- **Multiple Card Types**: Generate Basic, Cloze (fill-in-the-blank), and Reverse cards from the same content.
- **Duplicate Detection**: Warns you if a similar card already exists.
- **Image Embedding**: For diagrams and charts, the screenshot is embedded in your Anki card.
- **Web Dashboard**: Switch models, toggle settings, and see usage stats — open `ClaudeCards.app` or visit `localhost:8766`.
- **Cost Tracking**: Monitor your API usage and costs per day/month.
- **Offline Queue**: Cards are saved when Anki isn't running and delivered automatically when it opens.

## Requirements

- macOS
- Python 3.10+
- [Anki](https://apps.ankiweb.net/) with [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on
- Anthropic API key

## Installation

### 1. Clone and install dependencies

```bash
git clone https://github.com/taoburga/Claude_Cards.git
cd Claude_Cards
pip3 install -r requirements.txt
```

### 2. Configure your API key

**Option A: macOS Keychain (recommended)**
```bash
security add-generic-password -a ClaudeCards -s ClaudeCards -w 'sk-ant-your-key-here' -U
```

**Option B: Environment variable**
```bash
export ANTHROPIC_API_KEY='sk-ant-your-key-here'
```

**Option C: Config file**
```bash
cp config.example.json config.json
# Edit config.json and set anthropic_api_key
```

### 3. Install AnkiConnect

1. Open Anki
2. Go to **Tools > Add-ons > Get Add-ons**
3. Enter code: `2055492159`
4. Restart Anki

### 4. Set up the screenshot hotkey

1. Open **Automator** > Create new **Quick Action**
2. Set "Workflow receives" to **no input** in **any application**
3. Add a **Run Shell Script** action with:
   ```bash
   /path/to/Claude_Cards/capture_for_flashcard.sh
   ```
   (Use the full absolute path to where you cloned the repo)
4. Save as "Capture Flashcard Screenshot"
5. Go to **System Settings > Keyboard > Keyboard Shortcuts > Services**
6. Find your Quick Action and assign **Cmd+Shift+1**

### 5. Start the watcher

```bash
python3 flashcard_watcher.py
```

The watcher also serves a settings dashboard at `http://localhost:8766`. Open `ClaudeCards.app` or visit the URL directly to change settings and view usage stats.

### 6. (Optional) Auto-start on login

Edit the template LaunchAgent file in `launchagents/` to use your paths, then install:

```bash
# Edit the file: replace /path/to with your actual path
cp launchagents/com.claudecards.flashcardwatcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.claudecards.flashcardwatcher.plist
```

### 7. (Optional) Install the Chrome extension

1. Open Chrome > `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select the `browser_extension/` folder

## Usage

### Screenshot workflow
1. Open any article, PDF, or content you want to learn
2. (Optional) Highlight the key text you want to focus on
3. Press **Cmd+Shift+1**
4. Select the region to capture
5. Your flashcard appears in Anki (or is queued if Anki is closed)

### Browser extension workflow
1. Select text on any webpage
2. Right-click > "Create Flashcard"
3. Or click the extension icon and press "Create Flashcard"

### Alternative capture methods
- **Terminal**: `./capture_for_flashcard.sh`

### Settings & Usage
- **Dock app**: Click `ClaudeCards.app` to open the dashboard
- **Browser**: Visit `http://localhost:8766`

## Configuration

Edit `config.json` to customize:

| Setting | Description | Default |
|---------|-------------|---------|
| `model` | Claude model to use | `claude-sonnet-4-6` |
| `include_image` | When to embed screenshots | `auto` (only for diagrams) |
| `card_types` | Which card formats to create | `["basic"]` |
| `preview_before_save` | Show dialog before saving | `false` |
| `check_duplicates` | Warn about similar cards | `true` |
| `anki_deck` | Target Anki deck | `"Concepts"` |

Available models:
- `claude-haiku-4-5` - Fast and cheap (~$0.001/card)
- `claude-sonnet-4-6` - Balanced (recommended)
- `claude-opus-4-6` - Best quality

## Troubleshooting

### Hotkey not working
macOS Automator shortcuts can be flaky. Try these steps in order:
1. Run `/System/Library/CoreServices/pbs -update` in Terminal
2. Go to System Settings > Keyboard > Keyboard Shortcuts > Services > General
3. Find "Capture Flashcard Screenshot" and re-press Cmd+Shift+1 to rebind
4. If still not working, logout and login (full logout, not just lock)
5. As a workaround, run the script directly: `./capture_for_flashcard.sh`

### Cards not appearing in Anki
1. Make sure Anki is running
2. Check AnkiConnect: visit `http://localhost:8765` in browser
3. Verify deck name in config matches Anki
4. Check if cards are queued: look at `pending_cards.json`

### Extension not connecting
1. Check watcher is running: visit `http://localhost:8766` — if the dashboard loads, it's running
2. Verify `http://localhost:8766/status` returns `{"status": "running"}`

### Watcher not processing
1. Check logs: `tail -f flashcard_watcher.log`
2. Restart: stop the process and run `python3 flashcard_watcher.py` again

## License

MIT License - see LICENSE file

## Credits

Built with:
- [Anthropic Claude API](https://www.anthropic.com/)
- [AnkiConnect](https://github.com/FooSoft/anki-connect)
- [watchdog](https://github.com/gorakhargosh/watchdog)
