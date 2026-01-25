#!/bin/bash
# Capture screenshot for flashcard creation
# Triggered by Cmd+Shift+1 via Automator Quick Action

SCREENSHOTS_DIR="$HOME/Desktop/Claude_Cards/screenshots"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
FILENAME="flashcard_${TIMESTAMP}.png"

# Ensure directory exists
mkdir -p "$SCREENSHOTS_DIR"

# Capture interactive region selection
screencapture -i "$SCREENSHOTS_DIR/$FILENAME"

# Check if file was created (user didn't cancel)
if [ -f "$SCREENSHOTS_DIR/$FILENAME" ]; then
    # Optional: play sound to confirm capture
    afplay /System/Library/Sounds/Tink.aiff &
fi
