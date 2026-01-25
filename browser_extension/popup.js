// Claude Cards - Popup Script

const statusEl = document.getElementById('status');
const statusText = document.getElementById('status-text');
const createBtn = document.getElementById('create-btn');
const messageEl = document.getElementById('message');

// Check server status on load
async function checkStatus() {
  const isConnected = await chrome.runtime.sendMessage({ action: 'checkServer' });

  if (isConnected) {
    statusEl.className = 'status connected';
    statusText.textContent = 'Connected to Claude Cards';
    createBtn.disabled = false;
  } else {
    statusEl.className = 'status disconnected';
    statusText.textContent = 'Claude Cards not running';
    createBtn.disabled = true;
  }
}

// Show message
function showMessage(text, type) {
  messageEl.textContent = text;
  messageEl.className = `message ${type}`;
  messageEl.style.display = 'block';

  setTimeout(() => {
    messageEl.style.display = 'none';
  }, 5000);
}

// Create flashcard button click
createBtn.addEventListener('click', async () => {
  createBtn.disabled = true;
  createBtn.textContent = 'Creating...';

  const result = await chrome.runtime.sendMessage({ action: 'createFlashcard' });

  if (result.success) {
    showMessage('Flashcard created!', 'success');
  } else {
    showMessage(result.error || 'Failed to create flashcard', 'error');
  }

  createBtn.disabled = false;
  createBtn.textContent = 'Create Flashcard from Selection';
});

// Initialize
checkStatus();
