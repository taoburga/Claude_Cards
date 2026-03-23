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
import html as html_module
import logging
from logging.handlers import RotatingFileHandler
import subprocess
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

# Model pricing per 1M tokens (as of Feb 2026)
MODEL_PRICING = {
    'claude-haiku-4-5': {'input': 1.00, 'output': 5.00},
    'claude-sonnet-4-6': {'input': 3.00, 'output': 15.00},
    'claude-opus-4-6': {'input': 5.00, 'output': 25.00},
    # Legacy model IDs (still work)
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
    except Exception:
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
    pricing = MODEL_PRICING.get(model, MODEL_PRICING['claude-sonnet-4-6'])
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
    except Exception:
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


def format_source_html(source_info: dict) -> str:
    """Format a clean source line as HTML for the back of a card.

    Returns a small, hyperlinked domain name or empty string if source is
    useless (search engines, redirects, empty).
    """
    if not source_info:
        return ""

    url = source_info.get('url', '')
    domain = get_clean_domain(url)
    if not domain:
        return ""

    # Only allow http/https URLs to prevent javascript: and data: XSS
    try:
        scheme = urlparse(url).scheme.lower()
        if scheme not in ('http', 'https'):
            return ""
    except Exception:
        return ""

    # Escape for safe HTML attribute/content insertion
    safe_url = html_module.escape(url, quote=True)
    safe_domain = html_module.escape(domain)

    return (
        f'<br><br><small style="color: #888;">'
        f'<a href="{safe_url}" style="color: #888; text-decoration: none;">{safe_domain}</a>'
        f'</small>'
    )


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
    "description": "Create an Anki flashcard following the minimum information principle: one atomic fact per card, one unambiguous answer, context-free.",
    "input_schema": {
        "type": "object",
        "properties": {
            "front": {
                "type": "string",
                "description": "A precise question that tests recall of exactly ONE fact. Must constrain the answer space (not 'Tell me about X' but 'What mechanism causes X?'). No yes/no questions. Must be context-free (understandable without the source material)."
            },
            "back": {
                "type": "string",
                "description": "The answer: just the fact, no filler. Aim for under 15 words. If longer, the card probably needs splitting."
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 broad category tags, lowercase with hyphens (e.g. 'biology', 'machine-learning'). No page numbers or chapter references."
            },
            "cloze": {
                "type": "string",
                "description": "A single sentence with exactly ONE {{c1::key term}} deletion. Delete the important keyword, not filler words. Good: 'The mitochondria is the {{c1::powerhouse}} of the cell'. Bad: multiple deletions."
            },
            "reverse_front": {
                "type": "string",
                "description": "The definition/description that prompts recall of the term. Only useful when both directions matter (vocabulary, terminology)."
            },
            "reverse_back": {
                "type": "string",
                "description": "The term or concept being defined."
            },
            "has_diagram": {
                "type": "boolean",
                "description": "True ONLY if the screenshot contains a diagram, chart, graph, or visual that is essential to understanding the concept. A screenshot of text paragraphs is NOT a diagram."
            },
            "hint": {
                "type": "string",
                "description": "Optional 2-5 word mnemonic hint shown when the user is stuck (e.g. 'Think about energy...'). Only include if the card is non-obvious."
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


def create_flashcard_from_image(image_path: Path, source_info: dict = None,
                                selected_text: str = None, focus_prompt: str = None) -> dict:
    """Send image to Claude API and get flashcard content via tool_use."""
    client = anthropic.Anthropic(api_key=CONFIG['anthropic_api_key'])

    image_data = encode_image_to_base64(image_path)
    media_type = get_image_media_type(image_path)

    # Build prompt (follows minimum information principle and 20 rules of formulating knowledge)
    parts = [
        "Create a flashcard from this screenshot. Rules:\n"
        "- ONE atomic fact per card. If the content has multiple concepts, pick the most important one.\n"
        "- Front: a precise question with exactly one unambiguous correct answer. Test recall, not recognition.\n"
        "- Back: just the answer, under 15 words if possible. No filler.\n"
        "- The card must be understandable without seeing the screenshot.\n"
        "- Skip trivia. Focus on concepts, relationships, and mental models worth remembering."
    ]

    if focus_prompt:
        parts.append(f'\nThe user wants the card to focus on: "{focus_prompt}"')

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

    model = CONFIG.get('model', 'claude-sonnet-4-6')

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

    # Add source as a small hyperlinked domain on the back of the card
    source_html = format_source_html(source_info)
    if source_html:
        flashcard['back'] += source_html

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
            safe_filename = html_module.escape(image_filename, quote=True)
            image_html = f'\n\n<img src="{safe_filename}" style="max-width: 100%;">'
            logger.info(f"Stored image: {image_filename}")

    # Get configured card types
    card_types = CONFIG.get('card_types', ['basic'])
    cards_added = 0

    for card_type in card_types:
        try:
            if card_type == 'basic':
                # Standard Q&A card
                front_content = flashcard['front']
                hint = flashcard.get('hint', '')
                if hint:
                    safe_hint = html_module.escape(hint)
                    front_content += (
                        f'<br><br><details><summary style="color: #888; font-size: 0.8em; '
                        f'cursor: pointer;">Hint</summary>'
                        f'<span style="color: #888; font-size: 0.85em;">{safe_hint}</span></details>'
                    )
                back_content = flashcard['back'] + image_html
                note_data = {
                    'action': 'addNote',
                    'version': 6,
                    'params': {
                        'note': {
                            'deckName': CONFIG['anki_deck'],
                            'modelName': 'Basic',
                            'fields': {
                                'Front': front_content,
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


def ask_focus_prompt() -> str:
    """Show a quick dialog asking what the card should focus on.

    Returns the user's text, or empty string if they skip/cancel/dialog fails.
    Completely optional -- any failure silently returns empty string.
    """
    script = '''
    try
        set dialogResult to display dialog "What should the card focus on?" & return & "(Leave blank for auto)" default answer "" buttons {"Skip", "OK"} default button "OK" with title "Claude Cards" giving up after 30
        if gave up of dialogResult then
            return ""
        end if
        if button returned of dialogResult is "OK" then
            return text returned of dialogResult
        end if
        return ""
    on error
        return ""
    end try
    '''
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=35
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Focus prompt dialog skipped: {e}")
    return ""


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

        # Only show focus dialog if guided capture flag exists (Cmd+Shift+2)
        focus_flag = Path(CONFIG_PATH).parent / '.ask_focus'
        focus_prompt = ""
        if focus_flag.exists():
            focus_flag.unlink(missing_ok=True)
            focus_prompt = ask_focus_prompt()
            if focus_prompt:
                logger.info(f"User focus: {focus_prompt[:80]}")

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
        flashcard = create_flashcard_from_image(file_path, source_info, selected_text, focus_prompt)
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


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Cards</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --claude-orange: #E07A3A;
    --claude-orange-light: #F5A66A;
    --claude-orange-glow: rgba(224, 122, 58, 0.15);
    --claude-cream: #FDF6F0;
    --claude-warm-white: #FEFCFA;
    --claude-tan: #E8DDD3;
    --claude-brown: #6B5B4E;
    --claude-dark: #2D2420;
    --claude-text: #3D322A;
    --claude-text-light: #8B7D72;
    --claude-green: #5BA67A;
    --claude-red: #D45B5B;
    --radius: 16px;
    --radius-sm: 10px;
    --shadow: 0 2px 12px rgba(45, 36, 32, 0.08);
    --shadow-hover: 0 4px 20px rgba(45, 36, 32, 0.12);
  }

  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--claude-cream);
    color: var(--claude-text);
    min-height: 100vh;
    padding: 40px 20px;
  }

  .container {
    max-width: 640px;
    margin: 0 auto;
  }

  /* Header */
  .header {
    text-align: center;
    margin-bottom: 36px;
  }

  .logo {
    display: inline-flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
  }

  .logo-icon {
    width: 40px;
    height: 40px;
    background: var(--claude-orange);
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    color: white;
    box-shadow: 0 2px 8px rgba(224, 122, 58, 0.3);
  }

  .logo h1 {
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    font-size: 28px;
    font-weight: 700;
    color: var(--claude-dark);
    letter-spacing: -0.5px;
  }

  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 500;
    background: rgba(91, 166, 122, 0.12);
    color: var(--claude-green);
  }

  .status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--claude-green);
    animation: pulse 2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  /* Cards */
  .card {
    background: var(--claude-warm-white);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 16px;
    box-shadow: var(--shadow);
    border: 1px solid rgba(232, 221, 211, 0.6);
    transition: box-shadow 0.2s;
  }

  .card:hover { box-shadow: var(--shadow-hover); }

  .card-title {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--claude-text-light);
    margin-bottom: 16px;
  }

  /* Stats grid */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
  }

  .stat {
    text-align: center;
    padding: 16px 8px;
    background: var(--claude-cream);
    border-radius: var(--radius-sm);
  }

  .stat-value {
    font-size: 28px;
    font-weight: 700;
    color: var(--claude-dark);
    line-height: 1.1;
  }

  .stat-value.cost { color: var(--claude-orange); }

  .stat-label {
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--claude-text-light);
    margin-top: 4px;
  }

  /* Form elements */
  .field {
    margin-bottom: 16px;
  }

  .field:last-child { margin-bottom: 0; }

  .field label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: var(--claude-text);
    margin-bottom: 6px;
  }

  .field select, .field input[type="text"] {
    width: 100%;
    padding: 10px 14px;
    border: 1.5px solid var(--claude-tan);
    border-radius: var(--radius-sm);
    font-size: 14px;
    font-family: inherit;
    color: var(--claude-text);
    background: var(--claude-warm-white);
    transition: border-color 0.2s, box-shadow 0.2s;
    appearance: none;
    -webkit-appearance: none;
  }

  .field select {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 5l3 3 3-3' fill='none' stroke='%238B7D72' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    padding-right: 36px;
  }

  .field select:focus, .field input:focus {
    outline: none;
    border-color: var(--claude-orange);
    box-shadow: 0 0 0 3px var(--claude-orange-glow);
  }

  /* Checkbox group */
  .checkbox-group {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 500;
    border: 1.5px solid var(--claude-tan);
    background: var(--claude-warm-white);
    cursor: pointer;
    transition: all 0.2s;
    user-select: none;
  }

  .chip:hover { border-color: var(--claude-orange-light); }

  .chip.active {
    background: var(--claude-orange-glow);
    border-color: var(--claude-orange);
    color: var(--claude-orange);
  }

  .chip input { display: none; }

  /* Toggle */
  .toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 0;
  }

  .toggle-label {
    font-size: 14px;
    color: var(--claude-text);
  }

  .toggle {
    position: relative;
    width: 44px;
    height: 24px;
    cursor: pointer;
  }

  .toggle input { display: none; }

  .toggle-track {
    width: 100%;
    height: 100%;
    background: var(--claude-tan);
    border-radius: 12px;
    transition: background 0.2s;
  }

  .toggle input:checked + .toggle-track { background: var(--claude-orange); }

  .toggle-thumb {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 20px;
    height: 20px;
    background: white;
    border-radius: 50%;
    transition: transform 0.2s;
    box-shadow: 0 1px 3px rgba(0,0,0,0.15);
  }

  .toggle input:checked ~ .toggle-thumb { transform: translateX(20px); }

  /* Prompt */
  .field textarea {
    width: 100%;
    padding: 10px 14px;
    border: 1.5px solid var(--claude-tan);
    border-radius: var(--radius-sm);
    font-size: 13px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    color: var(--claude-text);
    background: var(--claude-warm-white);
    resize: vertical;
    min-height: 80px;
    line-height: 1.5;
    transition: border-color 0.2s, box-shadow 0.2s;
  }

  .field textarea:focus {
    outline: none;
    border-color: var(--claude-orange);
    box-shadow: 0 0 0 3px var(--claude-orange-glow);
  }

  /* Save button */
  .save-btn {
    width: 100%;
    padding: 12px;
    border: none;
    border-radius: var(--radius-sm);
    font-size: 15px;
    font-weight: 600;
    font-family: inherit;
    color: white;
    background: var(--claude-orange);
    cursor: pointer;
    transition: all 0.2s;
    margin-top: 20px;
    box-shadow: 0 2px 8px rgba(224, 122, 58, 0.25);
  }

  .save-btn:hover {
    background: var(--claude-orange-light);
    box-shadow: 0 4px 12px rgba(224, 122, 58, 0.35);
    transform: translateY(-1px);
  }

  .save-btn:active { transform: translateY(0); }

  .save-btn.saved {
    background: var(--claude-green);
    box-shadow: 0 2px 8px rgba(91, 166, 122, 0.25);
  }

  /* Footer */
  .footer {
    text-align: center;
    margin-top: 32px;
    font-size: 12px;
    color: var(--claude-text-light);
  }

  /* Toast */
  .toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%) translateY(80px);
    padding: 10px 20px;
    background: var(--claude-dark);
    color: white;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 500;
    opacity: 0;
    transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    pointer-events: none;
  }

  .toast.show {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="logo">
      <div class="logo-icon">&#9827;</div>
      <h1>Claude Cards</h1>
    </div>
    <div class="status-pill">
      <span class="status-dot"></span>
      Watcher running
    </div>
  </div>

  <div class="card" id="stats-card">
    <div class="card-title">Usage</div>
    <div class="stats-grid">
      <div class="stat">
        <div class="stat-value" id="cards-count">-</div>
        <div class="stat-label">Cards</div>
      </div>
      <div class="stat">
        <div class="stat-value cost" id="total-cost">-</div>
        <div class="stat-label">Total cost</div>
      </div>
      <div class="stat">
        <div class="stat-value" id="today-cards">-</div>
        <div class="stat-label">Today</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Settings</div>

    <div class="field">
      <label>Model</label>
      <select id="model">
        <option value="claude-haiku-4-5">Haiku &mdash; fast &amp; cheap</option>
        <option value="claude-sonnet-4-6">Sonnet &mdash; balanced</option>
        <option value="claude-opus-4-6">Opus &mdash; best quality</option>
      </select>
    </div>

    <div class="field">
      <label>Anki Deck</label>
      <input type="text" id="anki-deck" placeholder="Concepts">
    </div>

    <div class="field">
      <label>Include Image</label>
      <select id="include-image">
        <option value="auto">Auto (diagrams only)</option>
        <option value="always">Always</option>
        <option value="never">Never</option>
      </select>
    </div>

    <div class="field">
      <label>Card Types</label>
      <div class="checkbox-group">
        <label class="chip" data-type="basic">
          <input type="checkbox" value="basic"> Basic
        </label>
        <label class="chip" data-type="cloze">
          <input type="checkbox" value="cloze"> Cloze
        </label>
        <label class="chip" data-type="reverse">
          <input type="checkbox" value="reverse"> Reverse
        </label>
      </div>
    </div>

    <div class="field">
      <div class="toggle-row">
        <span class="toggle-label">Check for duplicates</span>
        <label class="toggle">
          <input type="checkbox" id="check-duplicates">
          <div class="toggle-track"></div>
          <div class="toggle-thumb"></div>
        </label>
      </div>
    </div>

    <div class="field">
      <div class="toggle-row">
        <span class="toggle-label">Preview before save</span>
        <label class="toggle">
          <input type="checkbox" id="preview-before-save">
          <div class="toggle-track"></div>
          <div class="toggle-thumb"></div>
        </label>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Prompt</div>
    <div class="field">
      <textarea id="prompt" rows="5"></textarea>
    </div>
  </div>

  <button class="save-btn" onclick="saveConfig()">Save Changes</button>

  <div class="footer">
    Listening on screenshots/ &middot; localhost:8766
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
  async function loadConfig() {
    try {
      const res = await fetch('/api/config');
      const cfg = await res.json();
      document.getElementById('model').value = cfg.model || 'claude-sonnet-4-6';
      document.getElementById('anki-deck').value = cfg.anki_deck || 'Concepts';
      document.getElementById('include-image').value = cfg.include_image || 'auto';
      document.getElementById('check-duplicates').checked = cfg.check_duplicates !== false;
      document.getElementById('preview-before-save').checked = !!cfg.preview_before_save;
      document.getElementById('prompt').value = cfg.prompt || '';

      const types = cfg.card_types || ['basic'];
      document.querySelectorAll('.chip').forEach(chip => {
        const cb = chip.querySelector('input');
        const active = types.includes(cb.value);
        cb.checked = active;
        chip.classList.toggle('active', active);
      });
    } catch (e) {
      showToast('Could not load config');
    }
  }

  async function loadUsage() {
    try {
      const res = await fetch('/api/usage');
      const stats = await res.json();
      document.getElementById('cards-count').textContent = stats.cards_created || 0;
      document.getElementById('total-cost').textContent =
        '$' + (stats.total_cost || 0).toFixed(2);
      const today = new Date().toISOString().split('T')[0];
      const todayStats = (stats.daily_stats || {})[today];
      document.getElementById('today-cards').textContent =
        todayStats ? todayStats.cards : 0;
    } catch (e) {
      // Usage file might not exist yet
    }
  }

  async function saveConfig() {
    const cardTypes = [];
    document.querySelectorAll('.chip input:checked').forEach(cb => cardTypes.push(cb.value));

    const config = {
      model: document.getElementById('model').value,
      anki_deck: document.getElementById('anki-deck').value,
      include_image: document.getElementById('include-image').value,
      card_types: cardTypes.length ? cardTypes : ['basic'],
      check_duplicates: document.getElementById('check-duplicates').checked,
      preview_before_save: document.getElementById('preview-before-save').checked,
      prompt: document.getElementById('prompt').value,
    };

    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(config),
      });
      if (res.ok) {
        const btn = document.querySelector('.save-btn');
        btn.textContent = 'Saved!';
        btn.classList.add('saved');
        setTimeout(() => { btn.textContent = 'Save Changes'; btn.classList.remove('saved'); }, 1500);
      } else {
        showToast('Save failed');
      }
    } catch (e) {
      showToast('Could not reach watcher');
    }
  }

  function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
  }

  // Chip toggle behavior
  document.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const cb = chip.querySelector('input');
      cb.checked = !cb.checked;
      chip.classList.toggle('active', cb.checked);
    });
  });

  loadConfig();
  loadUsage();
</script>
</body>
</html>
"""


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for browser extension and dashboard requests."""

    # Simple rate limiter: max 10 requests per 60 seconds
    _request_times = []
    _rate_lock = threading.Lock()
    _RATE_LIMIT = 10
    _RATE_WINDOW = 60  # seconds

    def _is_rate_limited(self) -> bool:
        """Check if request should be rejected due to rate limiting."""
        now = time.time()
        with self._rate_lock:
            # Purge old entries
            self._request_times[:] = [t for t in self._request_times if now - t < self._RATE_WINDOW]
            if len(self._request_times) >= self._RATE_LIMIT:
                return True
            self._request_times.append(now)
            return False

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
        """Handle GET requests."""
        if self.path == '/status':
            self.send_response(200)
            self.send_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {'status': 'running', 'version': '1.0'}
            self.wfile.write(json.dumps(response).encode())
        elif self.path == '/' or self.path == '/dashboard':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
        elif self.path == '/api/config':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            reload_config()
            safe_config = {k: v for k, v in CONFIG.items() if k != 'anthropic_api_key'}
            safe_config['has_api_key'] = bool(CONFIG.get('anthropic_api_key'))
            self.wfile.write(json.dumps(safe_config).encode())
        elif self.path == '/api/usage':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            stats = load_usage_stats()
            self.wfile.write(json.dumps(stats).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        """Handle POST requests."""
        if self.path == '/api/config':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 100_000:
                    self.send_response(413)
                    self.end_headers()
                    return
                body = self.rfile.read(content_length)
                updates = json.loads(body.decode())
                # Whitelist of editable fields
                allowed = {'anki_deck', 'model', 'include_image', 'card_types',
                           'preview_before_save', 'check_duplicates', 'prompt'}
                filtered = {k: v for k, v in updates.items() if k in allowed}
                # Load current config, merge, save
                try:
                    with open(CONFIG_PATH) as f:
                        current = json.load(f)
                except Exception:
                    current = {}
                current.update(filtered)
                with open(CONFIG_PATH, 'w') as f:
                    json.dump(current, f, indent=2)
                reload_config()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True}).encode())
            except Exception as e:
                logger.error(f"Config update error: {e}")
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Failed to save config'}).encode())
        elif self.path == '/create-flashcard':
            # Reject requests not from a Chrome extension
            origin = self.headers.get('Origin', '')
            if not origin.startswith('chrome-extension://'):
                self.send_response(403)
                self.end_headers()
                return

            # Rate limit to prevent API credit abuse
            if self._is_rate_limited():
                self.send_response(429)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Rate limit exceeded. Try again later.'}).encode())
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
        return {'error': 'Failed to create flashcard. Check watcher logs for details.'}


def create_flashcard_from_text(text: str, source_info: dict = None) -> dict:
    """Create a flashcard from text (no image) using Claude API with tool_use."""
    client = anthropic.Anthropic(api_key=CONFIG['anthropic_api_key'])

    parts = [
        "Create a flashcard from this text. Rules:\n"
        "- ONE atomic fact per card. Pick the most important concept.\n"
        "- Front: a precise question with exactly one unambiguous correct answer.\n"
        "- Back: just the answer, under 15 words if possible. No filler.\n"
        "- The card must be understandable without seeing the source text.\n"
        "- Skip trivia. Focus on concepts, relationships, and mental models worth remembering.\n"
        f'\nText:\n"{text}"'
    ]

    if source_info:
        parts.append(f"\nSource: {source_info.get('title', '')} - {source_info.get('url', '')}")

    model = CONFIG.get('model', 'claude-sonnet-4-6')

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

    # Add source as a small hyperlinked domain on the back of the card
    source_html = format_source_html(source_info)
    if source_html:
        flashcard['back'] += source_html

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
    screenshots_dir_str = CONFIG.get('screenshots_dir', '')
    if not screenshots_dir_str:
        # Default to screenshots/ subfolder if not configured
        screenshots_dir_str = str(Path(__file__).parent / 'screenshots')
        logger.info(f"No screenshots_dir configured, defaulting to: {screenshots_dir_str}")

    screenshots_dir = Path(screenshots_dir_str)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    if not screenshots_dir.is_dir():
        logger.error(f"Screenshots path is not a directory: {screenshots_dir}")
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
