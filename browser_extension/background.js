// Claude Cards - Background Service Worker

const API_URL = 'http://localhost:8766/create-flashcard';

// Create context menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'claude-cards-create',
    title: 'Create Flashcard',
    contexts: ['selection']
  });
});

// Handle context menu click
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === 'claude-cards-create') {
    await createFlashcard(info.selectionText, tab);
  }
});

// Handle messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'createFlashcard') {
    chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
      const tab = tabs[0];

      // Get selected text from page
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        function: () => window.getSelection().toString()
      }, async (results) => {
        const selectedText = results[0]?.result || '';
        const result = await createFlashcard(selectedText, tab);
        sendResponse(result);
      });
    });
    return true; // Keep channel open for async response
  }

  if (request.action === 'checkServer') {
    checkServerStatus().then(sendResponse);
    return true;
  }
});

async function createFlashcard(selectedText, tab) {
  const payload = {
    selected_text: selectedText,
    url: tab.url,
    title: tab.title,
    timestamp: new Date().toISOString()
  };

  try {
    const response = await fetch(API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      const result = await response.json();
      // Show notification
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icons/icon48.png',
        title: 'Flashcard Created',
        message: result.front?.substring(0, 100) || 'Card added to Anki'
      });
      return { success: true, data: result };
    } else {
      const error = await response.text();
      return { success: false, error: error };
    }
  } catch (error) {
    console.error('Failed to create flashcard:', error);
    return {
      success: false,
      error: 'Cannot connect to Claude Cards. Is the watcher running?'
    };
  }
}

async function checkServerStatus() {
  try {
    const response = await fetch('http://localhost:8766/status', {
      method: 'GET'
    });
    return response.ok;
  } catch {
    return false;
  }
}
