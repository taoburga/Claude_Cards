#!/usr/bin/env python3
"""
Claude Cards Menu Bar App
Provides status, model switching, and quick access to the flashcard system.
"""

import os
import json
import subprocess
import rumps
from pathlib import Path
from datetime import datetime

# Paths
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'config.json'
LOG_PATH = BASE_DIR / 'flashcard_watcher.log'
USAGE_PATH = BASE_DIR / 'usage_stats.json'
WATCHER_SCRIPT = BASE_DIR / 'flashcard_watcher.py'

# Available models
MODELS = {
    'Haiku (Fast)': 'claude-3-5-haiku-20241022',
    'Sonnet (Balanced)': 'claude-sonnet-4-20250514',
    'Opus (Best)': 'claude-opus-4-20250514',
}

# Image inclusion options
IMAGE_OPTIONS = {
    'Auto (diagrams only)': 'auto',
    'Always include': 'always',
    'Never include': 'never',
}

# Card types
CARD_TYPES = ['basic', 'cloze', 'reverse']


def load_config():
    """Load config from JSON file."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(config):
    """Save config to JSON file."""
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


def is_watcher_running():
    """Check if the flashcard watcher is running."""
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'flashcard_watcher.py'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def load_usage_stats():
    """Load usage statistics from file."""
    try:
        if USAGE_PATH.exists():
            with open(USAGE_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def get_last_card_from_log():
    """Parse log file to get last created card."""
    try:
        if not LOG_PATH.exists():
            return None
        with open(LOG_PATH, 'r') as f:
            lines = f.readlines()
        # Find last "Flashcard created" line
        for line in reversed(lines):
            if 'Flashcard created:' in line:
                # Extract card preview
                parts = line.split('Flashcard created:')
                if len(parts) > 1:
                    preview = parts[1].strip()
                    # Get timestamp
                    timestamp = line.split(' - ')[0]
                    return {'preview': preview, 'timestamp': timestamp}
        return None
    except Exception:
        return None


def is_anki_running():
    """Check if Anki is running."""
    try:
        result = subprocess.run(
            ['pgrep', '-x', 'Anki'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


class ClaudeCardsApp(rumps.App):
    def __init__(self):
        super().__init__(
            "Claude Cards",
            icon=None,  # Will use emoji/text
            quit_button=None  # Custom quit
        )
        self.config = load_config()
        self.setup_menu()

        # Update status periodically
        self.timer = rumps.Timer(self.update_status, 5)
        self.timer.start()

    def setup_menu(self):
        """Build the menu structure."""
        self.menu.clear()

        # Status section
        watcher_status = "Running" if is_watcher_running() else "Stopped"
        anki_status = "Running" if is_anki_running() else "Not Running"

        self.menu.add(rumps.MenuItem(f"Watcher: {watcher_status}", callback=None))
        self.menu.add(rumps.MenuItem(f"Anki: {anki_status}", callback=None))
        self.menu.add(rumps.separator)

        # Last card
        last_card = get_last_card_from_log()
        if last_card:
            preview = last_card['preview'][:50] + '...' if len(last_card['preview']) > 50 else last_card['preview']
            self.menu.add(rumps.MenuItem(f"Last: {preview}", callback=None))
        else:
            self.menu.add(rumps.MenuItem("No cards yet", callback=None))

        # Usage stats
        usage = load_usage_stats()
        if usage:
            today = datetime.now().strftime('%Y-%m-%d')
            today_stats = usage.get('daily_stats', {}).get(today, {})
            today_cost = today_stats.get('cost', 0)
            today_cards = today_stats.get('cards', 0)
            total_cost = usage.get('total_cost', 0)
            total_cards = usage.get('cards_created', 0)
            self.menu.add(rumps.MenuItem(
                f"Today: {today_cards} cards (${today_cost:.2f})",
                callback=None
            ))
            self.menu.add(rumps.MenuItem(
                f"Total: {total_cards} cards (${total_cost:.2f})",
                callback=None
            ))
        self.menu.add(rumps.separator)

        # Model switcher
        current_model = self.config.get('model', 'claude-sonnet-4-20250514')
        model_menu = rumps.MenuItem("Model")
        for name, model_id in MODELS.items():
            item = rumps.MenuItem(
                name,
                callback=lambda sender, m=model_id: self.switch_model(m)
            )
            if model_id == current_model:
                item.state = 1  # Checkmark
            model_menu.add(item)
        self.menu.add(model_menu)

        # Image inclusion option
        current_image_setting = self.config.get('include_image', 'auto')
        image_menu = rumps.MenuItem("Include Image")
        for name, setting in IMAGE_OPTIONS.items():
            item = rumps.MenuItem(
                name,
                callback=lambda sender, s=setting: self.switch_image_setting(s)
            )
            if setting == current_image_setting:
                item.state = 1  # Checkmark
            image_menu.add(item)
        self.menu.add(image_menu)

        # Card types submenu
        enabled_types = self.config.get('card_types', ['basic'])
        card_types_menu = rumps.MenuItem("Card Types")
        for card_type in CARD_TYPES:
            item = rumps.MenuItem(
                card_type.title(),
                callback=lambda sender, t=card_type: self.toggle_card_type(t)
            )
            item.state = 1 if card_type in enabled_types else 0
            card_types_menu.add(item)
        self.menu.add(card_types_menu)

        # Preview before save toggle
        preview_enabled = self.config.get('preview_before_save', False)
        preview_item = rumps.MenuItem(
            "Preview Before Save",
            callback=self.toggle_preview
        )
        preview_item.state = 1 if preview_enabled else 0
        self.menu.add(preview_item)

        # Duplicate check toggle
        dup_check_enabled = self.config.get('check_duplicates', True)
        dup_check_item = rumps.MenuItem(
            "Check for Duplicates",
            callback=self.toggle_dup_check
        )
        dup_check_item.state = 1 if dup_check_enabled else 0
        self.menu.add(dup_check_item)
        self.menu.add(rumps.separator)

        # Actions
        if is_watcher_running():
            self.menu.add(rumps.MenuItem("Stop Watcher", callback=self.stop_watcher))
        else:
            self.menu.add(rumps.MenuItem("Start Watcher", callback=self.start_watcher))

        self.menu.add(rumps.MenuItem("Open Screenshots Folder", callback=self.open_screenshots))
        self.menu.add(rumps.MenuItem("Open Config", callback=self.open_config))
        self.menu.add(rumps.MenuItem("View Log", callback=self.view_log))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=self.quit_app))

        # Update title based on status
        if is_watcher_running():
            self.title = "📚"  # Running
        else:
            self.title = "📚💤"  # Stopped

    def update_status(self, _):
        """Periodic status update."""
        self.setup_menu()

    def switch_model(self, model_id):
        """Switch the Claude model."""
        self.config['model'] = model_id
        save_config(self.config)

        # Restart watcher if running
        if is_watcher_running():
            self.stop_watcher(None)
            self.start_watcher(None)

        rumps.notification(
            "Model Changed",
            "",
            f"Now using: {model_id.split('-')[1].title()}",
            sound=False
        )
        self.setup_menu()

    def switch_image_setting(self, setting):
        """Switch the image inclusion setting."""
        self.config['include_image'] = setting
        save_config(self.config)

        setting_names = {v: k for k, v in IMAGE_OPTIONS.items()}
        rumps.notification(
            "Image Setting Changed",
            "",
            f"Now: {setting_names.get(setting, setting)}",
            sound=False
        )
        self.setup_menu()

    def toggle_preview(self, sender):
        """Toggle preview before save setting."""
        current = self.config.get('preview_before_save', False)
        self.config['preview_before_save'] = not current
        save_config(self.config)

        status = "enabled" if not current else "disabled"
        rumps.notification(
            "Preview Setting Changed",
            "",
            f"Preview before save: {status}",
            sound=False
        )
        self.setup_menu()

    def toggle_dup_check(self, sender):
        """Toggle duplicate checking."""
        current = self.config.get('check_duplicates', True)
        self.config['check_duplicates'] = not current
        save_config(self.config)

        status = "enabled" if not current else "disabled"
        rumps.notification(
            "Duplicate Check",
            "",
            f"Duplicate checking: {status}",
            sound=False
        )
        self.setup_menu()

    def toggle_card_type(self, card_type):
        """Toggle a card type on/off."""
        enabled_types = self.config.get('card_types', ['basic'])

        if card_type in enabled_types:
            # Don't allow disabling all types
            if len(enabled_types) > 1:
                enabled_types.remove(card_type)
                status = "disabled"
            else:
                rumps.notification(
                    "Cannot Disable",
                    "",
                    "At least one card type must be enabled",
                    sound=False
                )
                return
        else:
            enabled_types.append(card_type)
            status = "enabled"

        self.config['card_types'] = enabled_types
        save_config(self.config)

        rumps.notification(
            "Card Type Changed",
            "",
            f"{card_type.title()}: {status}",
            sound=False
        )
        self.setup_menu()

    def start_watcher(self, _):
        """Start the flashcard watcher."""
        try:
            subprocess.Popen(
                ['python3', str(WATCHER_SCRIPT)],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            rumps.notification("Claude Cards", "", "Watcher started", sound=False)
        except Exception as e:
            rumps.notification("Error", "", f"Could not start watcher: {e}")
        self.setup_menu()

    def stop_watcher(self, _):
        """Stop the flashcard watcher."""
        try:
            subprocess.run(['pkill', '-f', 'flashcard_watcher.py'])
            rumps.notification("Claude Cards", "", "Watcher stopped", sound=False)
        except Exception as e:
            rumps.notification("Error", "", f"Could not stop watcher: {e}")
        self.setup_menu()

    def open_screenshots(self, _):
        """Open screenshots folder in Finder."""
        screenshots_dir = BASE_DIR / 'screenshots'
        subprocess.run(['open', str(screenshots_dir)])

    def open_config(self, _):
        """Open config file in default editor."""
        subprocess.run(['open', str(CONFIG_PATH)])

    def view_log(self, _):
        """Open log file in Console."""
        subprocess.run(['open', '-a', 'Console', str(LOG_PATH)])

    def quit_app(self, _):
        """Quit the menu bar app (watcher keeps running)."""
        rumps.quit_application()


if __name__ == '__main__':
    ClaudeCardsApp().run()
