# Claude Cards

Create Anki flashcards from screenshots or selected text using Claude AI.

## Features

- **Screenshot to Flashcard**: Press `Cmd+Shift+1` to capture any part of your screen. Claude analyzes the image and creates a flashcard automatically.
- **Selection Detection**: Highlight text before screenshotting, and Claude focuses on what you selected.
- **Browser Extension**: Right-click selected text → "Create Flashcard" (no screenshot needed).
- **Smart Source Attribution**: Automatically captures the URL, cleans tracking parameters, and adds source to your cards.
- **Multiple Card Types**: Generate Basic, Cloze (fill-in-the-blank), and Reverse cards from the same content.
- **Duplicate Detection**: Warns you if a similar card already exists.
- **Image Embedding**: For diagrams and charts, the screenshot is embedded in your Anki card.
- **Menu Bar App**: Switch models, toggle settings, and see usage stats at a glance.
- **Cost Tracking**: Monitor your API usage and costs per day/month.

## Requirements

- macOS
- Python 3.10+
- [Anki](https://apps.ankiweb.net/) with [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on
- Anthropic API key

## Installation

### 1. Clone and Setup

```bash
git clone https://github.com/taoburga/Claude_Cards.git
cd Claude_Cards

# Install dependencies
pip3 install -r requirements.txt

# Copy and configure
cp config.example.json config.json
# Edit config.json and add your Anthropic API key
```

### 2. Install AnkiConnect

1. Open Anki
2. Go to **Tools → Add-ons → Get Add-ons**
3. Enter code: `2055492159`
4. Restart Anki

### 3. Create Screenshot Hotkey

1. Open **Automator** → Create new **Quick Action**
2. Set "Workflow receives" to **no input** in **any application**
3. Add **Run Shell Script** action:
   ```bash
   ~/path/to/claude-cards/capture_for_flashcard.sh
   ```
4. Save as "Capture Flashcard Screenshot"
5. Go to **System Settings → Keyboard → Keyboard Shortcuts → Services**
6. Find your Quick Action and assign **Cmd+Shift+1**

### 4. Start the Watcher

```bash
python3 flashcard_watcher.py
```

Or use the menu bar app:
```bash
python3 menubar_app.py
```

### 5. (Optional) Install Browser Extension

1. Open Chrome → `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select the `browser_extension/` folder

### 6. (Optional) Auto-Start on Login

Copy the LaunchAgent files to auto-start:
```bash
cp com.claudecards.flashcardwatcher.plist ~/Library/LaunchAgents/
cp com.claudecards.menubar.plist ~/Library/LaunchAgents/

# Load them
launchctl load ~/Library/LaunchAgents/com.claudecards.flashcardwatcher.plist
launchctl load ~/Library/LaunchAgents/com.claudecards.menubar.plist
```

## Usage

### Screenshot Workflow
1. Open any article, PDF, or content you want to learn
2. Highlight the key text you want to focus on (optional but recommended)
3. Press **Cmd+Shift+1**
4. Select the region to capture
5. Wait a moment - your flashcard will appear in Anki!

### Browser Extension Workflow
1. Select text on any webpage
2. Right-click → "Create Flashcard"
3. Or click the extension icon and press "Create Flashcard"

## Configuration

Edit `config.json` to customize:

| Setting | Description | Default |
|---------|-------------|---------|
| `model` | Claude model to use | `claude-sonnet-4-20250514` |
| `include_image` | When to embed screenshots | `auto` (only for diagrams) |
| `card_types` | Which card formats to create | `["basic"]` |
| `preview_before_save` | Show dialog before saving | `false` |
| `check_duplicates` | Warn about similar cards | `true` |

Available models:
- `claude-3-5-haiku-20241022` - Fast and cheap
- `claude-sonnet-4-20250514` - Balanced (recommended)
- `claude-opus-4-20250514` - Best quality

## Menu Bar App

The menu bar shows a 📚 icon with:
- Watcher status (running/stopped)
- Anki connection status
- Last card created
- Today's and total usage/cost
- Quick settings toggles
- Model switcher

## Files

```
claude-cards/
├── flashcard_watcher.py    # Main daemon
├── menubar_app.py          # Menu bar app
├── capture_for_flashcard.sh # Screenshot script
├── config.json             # Your configuration
├── requirements.txt        # Python dependencies
├── browser_extension/      # Chrome extension
│   ├── manifest.json
│   ├── background.js
│   ├── popup.html
│   ├── popup.js
│   └── icons/
└── screenshots/            # Captured screenshots
```

## Troubleshooting

### Hotkey not working
macOS Automator shortcuts can be flaky. Try these steps in order:
1. Run `/System/Library/CoreServices/pbs -update` in Terminal
2. Go to System Settings → Keyboard → Keyboard Shortcuts → Services → General
3. Find "Capture Flashcard Screenshot" and re-click to rebind Cmd+Shift+1
4. If still not working, logout and login (full logout)
5. As a workaround, run the script directly: `~/Desktop/Claude_Cards/capture_for_flashcard.sh`

### Cards not appearing in Anki
1. Make sure Anki is running
2. Check AnkiConnect: visit `http://localhost:8765` in browser
3. Verify deck name in config matches Anki

### Extension not connecting
1. Check watcher is running (look for 📚 in menu bar)
2. Verify `http://localhost:8766/status` returns `{"status": "running"}`

## License

MIT License - see LICENSE file

## Credits

Built with:
- [Anthropic Claude API](https://www.anthropic.com/)
- [AnkiConnect](https://github.com/FooSoft/anki-connect)
- [watchdog](https://github.com/gorakhargosh/watchdog)
- [rumps](https://github.com/jaredks/rumps)
