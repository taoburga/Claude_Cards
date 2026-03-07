#!/usr/bin/env python3
"""
Flashcard Watcher - Monitors screenshots folder and creates Anki flashcards
Uses Claude API for vision processing and AnkiConnect for card creation
"""

import os
import sys
import json
import time
import base64
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import re
import threading
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from collections import OrderedDict

# Hide Python from macOS Dock (must run before any AppKit usage)
try:
    import AppKit
    info = AppKit.NSBundle.mainBundle().infoDictionary()
    info["LSBackgroundOnly"] = "1"
except ImportError:
    pass
from http.server import HTTPServer, BaseHTTPRequestHandler

import anthropic
import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Setup logging with rotation (5MB per file, 3 backups = 20MB max)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_format)
logger.addHandler(_stream_handler)
_file_handler = RotatingFileHandler(
    Path(__file__).parent / 'flashcard_watcher.log',
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3
)
_file_handler.setFormatter(_log_format)
logger.addHandler(_file_handler)

# Paths
PID_PATH = Path(__file__).parent / 'watcher.pid'
CONFIG_PATH = Path(__file__).parent / 'config.json'
USAGE_PATH = Path(__file__).parent / 'usage_stats.json'
QUEUE_PATH = Path(__file__).parent / 'pending_cards.json'

# Load config initially, but reload before each operation
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)


def reload_config():
    """Reload config from disk so menu bar changes take effect immediately."""
    global CONFIG
    try:
        saved_api_key = CONFIG.get('anthropic_api_key', '')
        with open(CONFIG_PATH) as f:
            CONFIG = json.load(f)
        # Preserve the API key injected from keychain/env at startup
        if saved_api_key and not CONFIG.get('anthropic_api_key'):
            CONFIG['anthropic_api_key'] = saved_api_key
    except Exception as e:
        logger.warning(f"Could not reload config: {e}")

# Model pricing per 1M tokens (as of 2025)
MODEL_PRICING = {
    'claude-3-5-haiku-20241022': {'input': 0.80, 'output': 4.00},
    'claude-sonnet-4-20250514': {'input': 3.00, 'output': 15.00},
    'claude-opus-4-20250514': {'input': 15.00, 'output': 75.00},
}


def get_api_key_from_keychain() -> str:
    """Retrieve API key from macOS Keychain. Returns empty string if not found."""
    try:
        result = subprocess.run(
            ['security', 'find-generic-password', '-a', 'ClaudeCards', '-s', 'ClaudeCards', '-w'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ''


def load_pending_queue():
    """Load pending cards queue from file."""
    try:
        if QUEUE_PATH.exists():
            with open(QUEUE_PATH) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load pending queue: {e}")
    return []


def save_pending_queue(queue: list):
    """Save pending cards queue to file."""
    try:
        with open(QUEUE_PATH, 'w') as f:
            json.dump(queue, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save pending queue: {e}")


def add_to_queue(flashcard: dict, image_path: Path = None):
    """Add a flashcard to the pending queue for later processing."""
    queue = load_pending_queue()
    entry = {
        'flashcard': flashcard,
        'image_path': str(image_path) if image_path else None,
        'queued_at': datetime.now().isoformat()
    }
    queue.append(entry)
    save_pending_queue(queue)
    logger.info(f"Card queued for later: {flashcard['front'][:40]}... ({len(queue)} pending)")


def is_anki_available() -> bool:
    """Check if AnkiConnect is available."""
    try:
        response = requests.post(
            CONFIG['anki_connect_url'],
            json={'action': 'version', 'version': 6},
            timeout=2
        )
        return response.status_code == 200
    except:
        return False


def process_pending_queue():
    """Process any pending cards in the queue."""
    queue = load_pending_queue()
    if not queue:
        return

    if not is_anki_available():
        return

    logger.info(f"Processing {len(queue)} pending card(s)...")
    remaining = []

    for entry in queue:
        flashcard = entry['flashcard']
        image_path = Path(entry['image_path']) if entry.get('image_path') else None

        # Check if image still exists
        if image_path and not image_path.exists():
            image_path = None

        if add_to_anki_direct(flashcard, image_path):
            logger.info(f"Queued card added: {flashcard['front'][:40]}...")
        else:
            # Keep in queue if still failing
            remaining.append(entry)

    save_pending_queue(remaining)

    processed = len(queue) - len(remaining)
    if processed > 0:
        send_notification(
            "Queue Processed",
            f"Added {processed} pending card(s) to Anki"
        )


def anki_launch_watcher():
    """Wait for Anki to launch, then deliver queued cards.

    Edge-triggered: only processes queue on the transition from
    'Anki not running' to 'Anki running'. Sleeps longer when
    queue is empty (near-zero overhead).
    """
    anki_was_running = False
    while True:
        try:
            queue = load_pending_queue()
            if not queue:
                anki_was_running = False
                time.sleep(30)  # Nothing to deliver, sleep long
                continue

            # Queue has items -- check more frequently for Anki
            time.sleep(5)
            anki_running = is_anki_available()
            if anki_running and not anki_was_running:
                logger.info("Anki detected, processing pending queue...")
                process_pending_queue()
            anki_was_running = anki_running
        except Exception as e:
            logger.debug(f"Anki watcher: {e}")


def load_usage_stats():
    """Load usage statistics from file."""
    try:
        if USAGE_PATH.exists():
            with open(USAGE_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {
        'total_input_tokens': 0,
        'total_output_tokens': 0,
        'total_cost': 0.0,
        'cards_created': 0,
        'daily_stats': {},
        'last_updated': None
    }


def save_usage_stats(stats: dict):
    """Save usage statistics to file."""
    stats['last_updated'] = datetime.now().isoformat()
    with open(USAGE_PATH, 'w') as f:
        json.dump(stats, f, indent=2)


def track_usage(input_tokens: int, output_tokens: int, model: str):
    """Track API usage and estimated cost."""
    stats = load_usage_stats()

    # Update totals
    stats['total_input_tokens'] += input_tokens
    stats['total_output_tokens'] += output_tokens
    stats['cards_created'] += 1

    # Calculate cost
    pricing = MODEL_PRICING.get(model, MODEL_PRICING['claude-sonnet-4-20250514'])
    cost = (input_tokens / 1_000_000 * pricing['input']) + (output_tokens / 1_000_000 * pricing['output'])
    stats['total_cost'] += cost

    # Track daily stats
    today = datetime.now().strftime('%Y-%m-%d')
    if today not in stats['daily_stats']:
        stats['daily_stats'][today] = {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0, 'cards': 0}
    stats['daily_stats'][today]['input_tokens'] += input_tokens
    stats['daily_stats'][today]['output_tokens'] += output_tokens
    stats['daily_stats'][today]['cost'] += cost
    stats['daily_stats'][today]['cards'] += 1

    save_usage_stats(stats)
    logger.info(f"API cost: ${cost:.4f} (total: ${stats['total_cost']:.2f})")


def get_chrome_context():
    """Get Chrome tab URL, title, and selected text using AppleScript, only if Chrome is frontmost."""
    script = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
    end tell
    if frontApp is "Google Chrome" then
        tell application "Google Chrome"
            if (count of windows) > 0 then
                set activeTab to active tab of front window
                set tabUrl to URL of activeTab
                set tabTitle to title of activeTab
                -- Get selected text via JavaScript
                set selectedText to ""
                try
                    set selectedText to execute activeTab javascript "window.getSelection().toString();"
                end try
                return tabUrl & "|||" & tabTitle & "|||" & selectedText
            end if
        end tell
    else
        return ""
    end if
    '''
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split('|||')
            if len(parts) >= 2:
                return {
                    'url': parts[0],
                    'title': parts[1],
                    'selected_text': parts[2].strip() if len(parts) > 2 else ''
                }
    except Exception as e:
        logger.warning(f"Could not get Chrome context: {e}")
    return None


def clean_url(url: str) -> str:
    """Remove tracking parameters from URL."""
    if not url:
        return url

    # List of tracking parameters to remove
    tracking_params = {
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'utm_id', 'utm_cid', 'utm_reader', 'utm_name', 'utm_social', 'utm_social-type',
        'fbclid', 'gclid', 'gclsrc', 'dclid', 'msclkid',
        'mc_cid', 'mc_eid',  # Mailchimp
        'ref', 'ref_src', 'ref_url', 'referer', 'referrer',
        '_ga', '_gl', '_hsenc', '_hsmi',  # Google/HubSpot
        'ck_subscriber_id',  # ConvertKit
        'source', 'src',  # Generic source tracking
        'spm', 'share_source',  # Social tracking
        '__twitter_impression', 'twclid',  # Twitter
        'igshid',  # Instagram
        'si',  # Spotify/YouTube
        'feature', 'app',  # YouTube
    }

    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query, keep_blank_values=False)

        # Remove tracking parameters
        cleaned_params = {
            k: v for k, v in query_params.items()
            if k.lower() not in tracking_params and not k.lower().startswith('utm_')
        }

        # Rebuild URL
        cleaned_query = urlencode(cleaned_params, doseq=True)
        cleaned_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            cleaned_query,
            ''  # Remove fragment too for cleaner URLs
        ))

        # Remove trailing ? if no query params left
        cleaned_url = cleaned_url.rstrip('?')

        return cleaned_url
    except Exception as e:
        logger.warning(f"Could not clean URL: {e}")
        return url


def get_page_metadata():
    """Get Open Graph metadata from current Chrome page."""
    script = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
    end tell
    if frontApp is "Google Chrome" then
        tell application "Google Chrome"
            if (count of windows) > 0 then
                set activeTab to active tab of front window
                set metaScript to "
                    (function() {
                        var getMeta = function(name) {
                            var el = document.querySelector('meta[property=\"' + name + '\"]') ||
                                     document.querySelector('meta[name=\"' + name + '\"]');
                            return el ? el.getAttribute('content') : '';
                        };
                        return JSON.stringify({
                            author: getMeta('author') || getMeta('og:author') || getMeta('article:author'),
                            siteName: getMeta('og:site_name'),
                            publishDate: getMeta('article:published_time') || getMeta('date') || getMeta('publishedDate'),
                            description: getMeta('og:description') || getMeta('description')
                        });
                    })();
                "
                try
                    set metaResult to execute activeTab javascript metaScript
                    return metaResult
                end try
            end if
        end tell
    end if
    return "{}"
    '''
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Could not get page metadata: {e}")
    return {}


def get_clean_domain(url: str) -> str:
    """Extract clean domain name from URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '')
        # Skip if it looks like a search engine or redirect
        skip_domains = ['google.com', 'bing.com', 'duckduckgo.com', 'yahoo.com', 't.co', 'bit.ly']
        if any(skip in domain for skip in skip_domains):
            return ""
        return domain
    except:
        return ""


def format_source_attribution(source_info: dict, metadata: dict = None) -> str:
    """Format a clean source attribution string - just the domain name."""
    if not source_info:
        return ""

    url = source_info.get('url', '')

    # Get clean domain
    domain = get_clean_domain(url)
    if not domain:
        return ""

    # Check for site name from metadata (often cleaner than domain)
    site_name = metadata.get('siteName', '') if metadata else ''

    # Use site name if it's short and clean, otherwise use domain
    if site_name and len(site_name) < 30 and not site_name.startswith('http'):
        return site_name

    return domain


def encode_image_to_base64(image_path: Path) -> str:
    """Read image file and encode to base64."""
    with open(image_path, 'rb') as f:
        return base64.standard_b64encode(f.read()).decode('utf-8')


def get_image_media_type(image_path: Path) -> str:
    """Determine media type from file extension."""
    suffix = image_path.suffix.lower()
    media_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    return media_types.get(suffix, 'image/png')


# Tool schema for structured flashcard output
FLASHCARD_TOOL = {
    "name": "create_flashcard",
    "description": "Create an Anki flashcard from the provided content.",
    "input_schema": {
        "type": "object",
        "properties": {
            "front": {
                "type": "string",
                "description": "A clear, concise question or prompt that tests understanding of the concept"
            },
            "back": {
                "type": "string",
                "description": "The answer or explanation - be concise but complete"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 relevant tags for categorization"
            },
            "cloze": {
                "type": "string",
                "description": "A sentence with {{c1::key term}} blanked out for cloze deletion cards"
            },
            "reverse_front": {
                "type": "string",
                "description": "The answer/definition that prompts recall of the term (for reverse cards)"
            },
            "reverse_back": {
                "type": "string",
                "description": "The term or concept being defined (for reverse cards)"
            },
            "has_diagram": {
                "type": "boolean",
                "description": "Whether the screenshot contains a diagram, chart, or visual that should be embedded in the card"
            }
        },
        "required": ["front", "back", "tags"]
    }
}


def _call_with_retry(fn, max_retries=3):
    """Call a function with exponential backoff on failure."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(f"API call failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait}s...")
            time.sleep(wait)


def _extract_flashcard_from_response(response) -> dict:
    """Extract flashcard dict from a tool_use response."""
    for block in response.content:
        if block.type == "tool_use" and block.name == "create_flashcard":
            return block.input
    raise ValueError("Claude did not return a flashcard via tool_use")


def create_flashcard_from_image(image_path: Path, source_info: dict = None, selected_text: str = None) -> dict:
    """Send image to Claude API and get flashcard content via tool_use."""
    client = anthropic.Anthropic(api_key=CONFIG['anthropic_api_key'])

    image_data = encode_image_to_base64(image_path)
    media_type = get_image_media_type(image_path)

    # Build prompt
    parts = ["Analyze this screenshot and create a flashcard for learning/memorization. "
             "Extract the key concept. Front should test recall, not just recognition. "
             "Keep both sides concise."]

    if selected_text:
        parts.append(f'\nThe user has highlighted this text: "{selected_text}"\n'
                     'Focus the flashcard on this selected content specifically.')

    if source_info:
        parts.append(f"\nSource context - URL: {source_info.get('url', 'Unknown')}, "
                     f"Page title: {source_info.get('title', 'Unknown')}")

    # Allow custom prompt override but append it rather than replacing
    custom_prompt = CONFIG.get('prompt', '')
    if custom_prompt:
        parts.append(f"\nAdditional instructions: {custom_prompt}")

    prompt = '\n'.join(parts)

    model = CONFIG.get('model', 'claude-sonnet-4-20250514')

    def _api_call():
        return client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[FLASHCARD_TOOL],
            tool_choice={"type": "tool", "name": "create_flashcard"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )

    response = _call_with_retry(_api_call)

    if hasattr(response, 'usage'):
        track_usage(response.usage.input_tokens, response.usage.output_tokens, model)

    flashcard = _extract_flashcard_from_response(response)

    # Add source info to the back if available (clean domain only)
    if source_info and source_info.get('url'):
        domain = get_clean_domain(source_info['url'])
        if domain:
            flashcard['back'] += f"\n\nSource: {domain}"

    if source_info:
        flashcard['source_url'] = source_info.get('url', '')
        flashcard['source_attribution'] = source_info.get('attribution', '')

    return flashcard


def check_for_duplicates(front_text: str) -> list:
    """Check if similar cards already exist in Anki."""
    try:
        # Search for cards with similar front text
        # Use first 50 chars to find potential duplicates
        search_text = front_text[:50].replace('"', '\\"')

        response = requests.post(
            CONFIG['anki_connect_url'],
            json={
                'action': 'findNotes',
                'version': 6,
                'params': {
                    'query': f'deck:"{CONFIG["anki_deck"]}" front:*{search_text[:30]}*'
                }
            }
        )
        result = response.json()

        if result.get('error') or not result.get('result'):
            return []

        note_ids = result['result']
        if not note_ids:
            return []

        # Get note info for found cards
        response = requests.post(
            CONFIG['anki_connect_url'],
            json={
                'action': 'notesInfo',
                'version': 6,
                'params': {'notes': note_ids[:5]}  # Limit to 5 potential duplicates
            }
        )
        result = response.json()

        if result.get('error'):
            return []

        duplicates = []
        for note in result.get('result', []):
            fields = note.get('fields', {})
            existing_front = fields.get('Front', {}).get('value', '')
            # Simple similarity check - if first 30 chars match
            if existing_front and front_text[:30].lower() in existing_front.lower():
                duplicates.append({
                    'id': note['noteId'],
                    'front': existing_front[:100]
                })

        return duplicates

    except Exception as e:
        logger.warning(f"Duplicate check failed: {e}")
        return []


def show_duplicate_warning(duplicates: list, new_front: str) -> str:
    """Show warning about potential duplicates, return 'save' or 'skip'."""
    dup_preview = _sanitize_for_applescript(duplicates[0]['front'][:80]) if duplicates else ""
    new_preview = _sanitize_for_applescript(new_front[:80])

    script = f'''
    set theDialog to display dialog "Potential duplicate found!\\n\\nExisting card:\\n{dup_preview}...\\n\\nNew card:\\n{new_preview}..." with title "Duplicate Warning" buttons {{"Skip", "Save Anyway"}} default button "Skip" with icon caution
    return button returned of theDialog
    '''

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            choice = result.stdout.strip()
            return "skip" if choice == "Skip" else "save"
        return "save"
    except Exception as e:
        logger.warning(f"Duplicate warning dialog failed: {e}")
        return "save"


def store_image_in_anki(image_path: Path) -> str:
    """Store image in Anki's media folder and return the filename."""
    try:
        # Generate unique filename
        filename = f"claude_cards_{image_path.stem}_{int(time.time())}{image_path.suffix}"

        # Read and encode image
        image_data = encode_image_to_base64(image_path)

        # Store via AnkiConnect
        response = requests.post(
            CONFIG['anki_connect_url'],
            json={
                'action': 'storeMediaFile',
                'version': 6,
                'params': {
                    'filename': filename,
                    'data': image_data
                }
            }
        )
        result = response.json()
        if result.get('error'):
            logger.error(f"Failed to store image: {result['error']}")
            return None
        return filename
    except Exception as e:
        logger.error(f"Error storing image in Anki: {e}")
        return None


def add_to_anki(flashcard: dict, image_path: Path = None) -> bool:
    """Add flashcard to Anki, or queue it if Anki is unavailable."""
    if not is_anki_available():
        logger.warning("Anki not available, queueing card for later")
        add_to_queue(flashcard, image_path)
        return False  # Return False to indicate card wasn't added yet
    return add_to_anki_direct(flashcard, image_path)


def add_to_anki_direct(flashcard: dict, image_path: Path = None) -> bool:
    """Add flashcard(s) to Anki via AnkiConnect based on configured card types."""
    # First, check if AnkiConnect is running
    try:
        response = requests.post(
            CONFIG['anki_connect_url'],
            json={'action': 'version', 'version': 6}
        )
        if response.status_code != 200:
            logger.error("AnkiConnect not responding")
            return False
    except requests.exceptions.ConnectionError:
        logger.error("Cannot connect to AnkiConnect. Is Anki running?")
        return False

    # Ensure deck exists
    requests.post(
        CONFIG['anki_connect_url'],
        json={
            'action': 'createDeck',
            'version': 6,
            'params': {'deck': CONFIG['anki_deck']}
        }
    )

    # Determine if we should include the image
    include_image_setting = CONFIG.get('include_image', 'auto')
    should_include_image = False

    if include_image_setting == 'always':
        should_include_image = True
    elif include_image_setting == 'never':
        should_include_image = False
    elif include_image_setting == 'auto':
        should_include_image = flashcard.get('has_diagram', False)

    # Store image if needed
    image_html = ""
    if should_include_image and image_path:
        image_filename = store_image_in_anki(image_path)
        if image_filename:
            image_html = f'\n\n<img src="{image_filename}" style="max-width: 100%;">'
            logger.info(f"Stored image: {image_filename}")

    # Get configured card types
    card_types = CONFIG.get('card_types', ['basic'])
    cards_added = 0

    for card_type in card_types:
        try:
            if card_type == 'basic':
                # Standard Q&A card
                back_content = flashcard['back'] + image_html
                note_data = {
                    'action': 'addNote',
                    'version': 6,
                    'params': {
                        'note': {
                            'deckName': CONFIG['anki_deck'],
                            'modelName': 'Basic',
                            'fields': {
                                'Front': flashcard['front'],
                                'Back': back_content
                            },
                            'tags': flashcard.get('tags', []) + ['claude-cards', 'basic']
                        }
                    }
                }
                response = requests.post(CONFIG['anki_connect_url'], json=note_data)
                result = response.json()
                if not result.get('error'):
                    cards_added += 1
                    logger.info(f"Added basic card: {flashcard['front'][:40]}...")
                else:
                    logger.error(f"Basic card error: {result['error']}")

            elif card_type == 'cloze' and flashcard.get('cloze'):
                # Cloze deletion card
                cloze_content = flashcard['cloze'] + image_html
                note_data = {
                    'action': 'addNote',
                    'version': 6,
                    'params': {
                        'note': {
                            'deckName': CONFIG['anki_deck'],
                            'modelName': 'Cloze',
                            'fields': {
                                'Text': cloze_content,
                                'Extra': flashcard.get('back', '')
                            },
                            'tags': flashcard.get('tags', []) + ['claude-cards', 'cloze']
                        }
                    }
                }
                response = requests.post(CONFIG['anki_connect_url'], json=note_data)
                result = response.json()
                if not result.get('error'):
                    cards_added += 1
                    logger.info(f"Added cloze card")
                else:
                    logger.error(f"Cloze card error: {result['error']}")

            elif card_type == 'reverse' and flashcard.get('reverse_front'):
                # Reverse card (answer prompts term)
                reverse_back = flashcard.get('reverse_back', flashcard['front']) + image_html
                note_data = {
                    'action': 'addNote',
                    'version': 6,
                    'params': {
                        'note': {
                            'deckName': CONFIG['anki_deck'],
                            'modelName': 'Basic',
                            'fields': {
                                'Front': flashcard['reverse_front'],
                                'Back': reverse_back
                            },
                            'tags': flashcard.get('tags', []) + ['claude-cards', 'reverse']
                        }
                    }
                }
                response = requests.post(CONFIG['anki_connect_url'], json=note_data)
                result = response.json()
                if not result.get('error'):
                    cards_added += 1
                    logger.info(f"Added reverse card")
                else:
                    logger.error(f"Reverse card error: {result['error']}")

        except Exception as e:
            logger.error(f"Error adding {card_type} card: {e}")

    if cards_added > 0:
        logger.info(f"Added {cards_added} card(s) to Anki")
        return True
    return False


def _sanitize_for_applescript(text: str) -> str:
    """Sanitize text for safe use in AppleScript strings.

    Strips all characters that could break out of an AppleScript quoted string.
    """
    # Remove backslashes and double quotes entirely (safest approach)
    text = text.replace('\\', '').replace('"', "'")
    # Remove control characters except newline
    text = ''.join(c for c in text if c == '\n' or (ord(c) >= 32 and ord(c) < 127) or ord(c) > 127)
    return text


def send_notification(title: str, message: str):
    """Send macOS notification."""
    title = _sanitize_for_applescript(title)[:100]
    message = _sanitize_for_applescript(message)[:200]
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(['osascript', '-e', script], capture_output=True, timeout=5)


def show_preview_dialog(flashcard: dict) -> str:
    """Show a preview dialog and return user choice: 'save', 'skip', or 'edit'."""
    front = _sanitize_for_applescript(flashcard['front'])[:300]
    back_preview = _sanitize_for_applescript(flashcard['back'][:200])
    if len(flashcard['back']) > 200:
        back_preview += '...'

    dialog_text = f"FRONT:\\n{front}\\n\\nBACK:\\n{back_preview}"
    script = f'''
    set theDialog to display dialog "{dialog_text}" with title "Flashcard Preview" buttons {{"Skip", "Edit in Anki", "Save"}} default button "Save" with icon note
    return button returned of theDialog
    '''

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            choice = result.stdout.strip()
            if choice == "Save":
                return "save"
            elif choice == "Skip":
                return "skip"
            elif choice == "Edit in Anki":
                return "edit"
        return "save"
    except subprocess.TimeoutExpired:
        logger.warning("Preview dialog timed out, saving card")
        return "save"
    except Exception as e:
        logger.error(f"Preview dialog error: {e}")
        return "save"


class ScreenshotHandler(FileSystemEventHandler):
    """Handle new screenshot files."""

    MAX_TRACKED_FILES = 1000

    def __init__(self):
        self.processed_files = OrderedDict()  # bounded, preserves insertion order
        self.processing = set()
        self._lock = threading.Lock()

    def on_created(self, event):
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Only process image files
        if file_path.suffix.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
            return

        # Thread-safe guard against duplicate events from watchdog
        with self._lock:
            if file_path in self.processed_files or file_path in self.processing:
                return
            self.processing.add(file_path)

        # Wait until file size stabilizes (fully written to disk)
        prev_size = -1
        for _ in range(20):  # up to 10 seconds (20 * 0.5s)
            time.sleep(0.5)
            try:
                curr_size = file_path.stat().st_size
            except OSError:
                break
            if curr_size == prev_size and curr_size > 0:
                break
            prev_size = curr_size

        try:
            self.process_screenshot(file_path)
            self.processed_files[file_path] = True
            # Evict oldest entries to bound memory usage
            while len(self.processed_files) > self.MAX_TRACKED_FILES:
                self.processed_files.popitem(last=False)
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            send_notification("Flashcard Error", f"Failed to process screenshot: {str(e)[:50]}")
        finally:
            self.processing.discard(file_path)

    def process_screenshot(self, file_path: Path):
        """Process a screenshot and create a flashcard."""
        reload_config()
        logger.info(f"Processing: {file_path.name}")

        # Get Chrome context: URL, title, and selected text
        chrome_context = get_chrome_context()
        source_info = None
        selected_text = None
        metadata = None

        if chrome_context:
            # Clean the URL
            clean_source_url = clean_url(chrome_context['url'])
            source_info = {
                'url': clean_source_url,
                'title': chrome_context['title'],
                'original_url': chrome_context['url']
            }
            selected_text = chrome_context.get('selected_text', '')

            # Get page metadata (author, date, etc.)
            metadata = get_page_metadata()
            source_info['metadata'] = metadata

            # Format nice attribution
            source_info['attribution'] = format_source_attribution(source_info, metadata)

            logger.info(f"Source: {source_info.get('attribution', source_info.get('title', 'Unknown'))}")
            if selected_text:
                logger.info(f"Selected text: {selected_text[:100]}{'...' if len(selected_text) > 100 else ''}")

        # Create flashcard using Claude
        logger.info("Sending to Claude API...")
        flashcard = create_flashcard_from_image(file_path, source_info, selected_text)
        logger.info(f"Flashcard created: {flashcard['front'][:50]}...")

        # Check for duplicates
        if CONFIG.get('check_duplicates', True):
            duplicates = check_for_duplicates(flashcard['front'])
            if duplicates:
                logger.warning(f"Found {len(duplicates)} potential duplicate(s)")
                choice = show_duplicate_warning(duplicates, flashcard['front'])
                if choice == "skip":
                    logger.info("User skipped duplicate card")
                    send_notification("Duplicate Skipped", "Similar card already exists")
                    return

        # Preview before saving if enabled
        if CONFIG.get('preview_before_save', False):
            choice = show_preview_dialog(flashcard)
            if choice == "skip":
                logger.info("User skipped card")
                send_notification("Card Skipped", "Flashcard was not saved")
                return
            elif choice == "edit":
                # Save to Anki but open browser for editing
                logger.info("User chose to edit card")

        # Add to Anki (will be queued if Anki is offline)
        if add_to_anki(flashcard, file_path):
            send_notification(
                "Flashcard Created",
                f"{flashcard['front'][:50]}..."
            )
        else:
            # Card was queued
            pending_count = len(load_pending_queue())
            send_notification(
                "Flashcard Queued",
                f"Anki offline. {pending_count} card(s) pending."
            )


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for browser extension requests."""

    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.debug(f"HTTP: {format % args}")

    def send_cors_headers(self):
        """Send CORS headers for browser extension only."""
        origin = self.headers.get('Origin', '')
        # Only allow requests from our Chrome extension
        if origin.startswith('chrome-extension://'):
            self.send_header('Access-Control-Allow-Origin', origin)
        # Block all other origins (no header = browser blocks the response)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        """Handle preflight CORS requests."""
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        """Handle GET requests (status check)."""
        if self.path == '/status':
            self.send_response(200)
            self.send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {'status': 'running', 'version': '1.0'}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        """Handle POST requests (create flashcard)."""
        if self.path == '/create-flashcard':
            # Reject requests not from a Chrome extension
            origin = self.headers.get('Origin', '')
            if not origin.startswith('chrome-extension://'):
                self.send_response(403)
                self.end_headers()
                return

            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 1_000_000:  # 1MB max
                    self.send_response(413)
                    self.end_headers()
                    return
                body = self.rfile.read(content_length)
                data = json.loads(body.decode())

                logger.info(f"Extension request: {data.get('title', 'Unknown')[:50]}")

                # Process the request
                result = process_extension_request(data)

                self.send_response(200)
                self.send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            except Exception as e:
                logger.error(f"Extension request error: {e}")
                self.send_response(500)
                self.send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Internal error'}).encode())
        else:
            self.send_response(404)
            self.end_headers()


def process_extension_request(data: dict) -> dict:
    """Process a flashcard request from the browser extension."""
    reload_config()
    selected_text = data.get('selected_text', '')
    url = data.get('url', '')
    title = data.get('title', '')

    if not selected_text:
        return {'error': 'No text selected'}

    # Clean the URL
    clean_source_url = clean_url(url)
    source_info = {
        'url': clean_source_url,
        'title': title,
        'attribution': title or urlparse(url).netloc
    }

    # Create flashcard from text (no image)
    try:
        flashcard = create_flashcard_from_text(selected_text, source_info)

        # Check for duplicates
        if CONFIG.get('check_duplicates', True):
            duplicates = check_for_duplicates(flashcard['front'])
            if duplicates:
                logger.warning(f"Potential duplicate found for extension request")
                # For extension, we'll still save but log warning

        # Add to Anki (will be queued if Anki is offline)
        if add_to_anki(flashcard, None):
            send_notification("Flashcard Created", f"{flashcard['front'][:50]}...")
            return {
                'success': True,
                'front': flashcard['front'],
                'back': flashcard['back']
            }
        else:
            # Card was queued
            pending_count = len(load_pending_queue())
            send_notification("Flashcard Queued", f"Anki offline. {pending_count} card(s) pending.")
            return {
                'success': True,
                'queued': True,
                'front': flashcard['front'],
                'back': flashcard['back'],
                'message': f'Card queued. {pending_count} pending.'
            }

    except Exception as e:
        logger.error(f"Error processing extension request: {e}")
        return {'error': str(e)}


def create_flashcard_from_text(text: str, source_info: dict = None) -> dict:
    """Create a flashcard from text (no image) using Claude API with tool_use."""
    client = anthropic.Anthropic(api_key=CONFIG['anthropic_api_key'])

    parts = [f'Create a flashcard for learning/memorization from this text:\n\n"{text}"']
    parts.append("Focus on the key concept. Front should test recall, not just recognition. Keep both sides concise.")

    if source_info:
        parts.append(f"\nSource: {source_info.get('title', '')} - {source_info.get('url', '')}")

    model = CONFIG.get('model', 'claude-sonnet-4-20250514')

    def _api_call():
        return client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[FLASHCARD_TOOL],
            tool_choice={"type": "tool", "name": "create_flashcard"},
            messages=[{"role": "user", "content": '\n'.join(parts)}]
        )

    response = _call_with_retry(_api_call)

    if hasattr(response, 'usage'):
        track_usage(response.usage.input_tokens, response.usage.output_tokens, model)

    flashcard = _extract_flashcard_from_response(response)

    # Add source (clean domain only)
    if source_info and source_info.get('url'):
        domain = get_clean_domain(source_info['url'])
        if domain:
            flashcard['back'] += f"\n\nSource: {domain}"

    return flashcard


def start_extension_server():
    """Start HTTP server for browser extension in background thread."""
    port = CONFIG.get('extension_port', 8766)
    try:
        server = HTTPServer(('127.0.0.1', port), ExtensionRequestHandler)
        logger.info(f"Extension server listening on http://localhost:{port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Could not start extension server: {e}")


def main():
    """Main entry point."""
    screenshots_dir = Path(CONFIG['screenshots_dir'])

    if not screenshots_dir.exists():
        logger.error(f"Screenshots directory does not exist: {screenshots_dir}")
        sys.exit(1)

    # Get API key: Keychain > env var > config.json
    api_key = get_api_key_from_keychain()
    if not api_key:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        api_key = CONFIG.get('anthropic_api_key', '')
        if api_key == 'YOUR_API_KEY_HERE':
            api_key = ''
    if not api_key:
        logger.error(
            "No API key found. Set it using one of:\n"
            "  1. macOS Keychain: security add-generic-password -a ClaudeCards -s ClaudeCards -w 'sk-ant-...' -U\n"
            "  2. Environment: export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  3. config.json: set anthropic_api_key field"
        )
        sys.exit(1)
    CONFIG['anthropic_api_key'] = api_key

    # Write PID file for clean process management
    PID_PATH.write_text(str(os.getpid()))

    logger.info(f"Watching: {screenshots_dir}")
    logger.info(f"Anki deck: {CONFIG['anki_deck']}")
    logger.info("Press Ctrl+C to stop")

    # Process any pending cards from queue on startup
    pending = load_pending_queue()
    if pending:
        logger.info(f"Found {len(pending)} pending card(s) in queue")
        process_pending_queue()

    # Start extension server in background thread
    extension_thread = threading.Thread(target=start_extension_server, daemon=True)
    extension_thread.start()

    # Start Anki launch watcher in background thread
    queue_thread = threading.Thread(target=anki_launch_watcher, daemon=True)
    queue_thread.start()

    event_handler = ScreenshotHandler()
    observer = Observer()
    observer.schedule(event_handler, str(screenshots_dir), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping watcher...")
        observer.stop()
    finally:
        # Clean up PID file
        try:
            PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass

    observer.join()


if __name__ == '__main__':
    main()
