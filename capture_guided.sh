#!/bin/bash
# Capture screenshot with focus prompt dialog
# Triggered by Cmd+Shift+2 via Automator Quick Action

# Resolve the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCREENSHOTS_DIR="$SCRIPT_DIR/screenshots"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
FILENAME="flashcard_${TIMESTAMP}.png"

# Ensure directory exists
mkdir -p "$SCREENSHOTS_DIR"

# Set flag so the watcher knows to show the focus dialog
touch "$SCRIPT_DIR/.ask_focus"

# Capture interactive region selection
screencapture -i "$SCREENSHOTS_DIR/$FILENAME"

# Check if file was created (user didn't cancel)
if [ -f "$SCREENSHOTS_DIR/$FILENAME" ]; then
    afplay /System/Library/Sounds/Tink.aiff &
else
    # User cancelled, remove the flag
    rm -f "$SCRIPT_DIR/.ask_focus"
fi
