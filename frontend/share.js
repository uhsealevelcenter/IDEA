/**
 * Shared Conversation Viewer
 * Handles loading and displaying shared conversations in read-only mode
 */

const sharedStdoutMap = new Map();
const sharedMessageCache = new Map();
let lastSharedCodeId = null;
const SHARED_STD_STREAM_RECIPIENTS = ['stdout', 'stderr'];

//// Math formatting helpers for shared/downloaded views
function protectMath(text) {
    const store = [];
    const protect = (regex) => (src) =>
        src.replace(regex, (match) => {
            const key = `@@MATH${store.length}@@`;
            store.push(match);
            return key;
        });

    let out = protect(/\$\$([\s\S]*?)\$\$/g)(text);
    out = protect(/\\\[([\s\S]*?)\\\]/g)(out);
    out = protect(/(?<!\$)\$([^\n]+?)\$(?!\$)/g)(out);
    out = protect(/\\\(([^\n]+?)\\\)/g)(out);

    return { text: out, store };
}

function restoreMath(html, store) {
    return store.reduce((acc, original, index) => acc.replace(`@@MATH${index}@@`, original), html);
}

function countUnescapedSequence(text, sequence) {
    if (!text || !sequence) return 0;
    let count = 0;
    let index = text.indexOf(sequence);
    while (index !== -1) {
        let backslashCount = 0;
        let cursor = index - 1;
        while (cursor >= 0 && text[cursor] === '\\') {
            backslashCount += 1;
            cursor -= 1;
        }
        if (backslashCount % 2 === 0) {
            count += 1;
        }
        index = text.indexOf(sequence, index + sequence.length);
    }
    return count;
}

function hasBalancedMath(text) {
    const dollars = countUnescapedSequence(text, '$$') % 2 === 0;
    const lb = (text.match(/\\\[/g) || []).length;
    const rb = (text.match(/\\\]/g) || []).length;
    const lp = (text.match(/\\\(/g) || []).length;
    const rp = (text.match(/\\\)/g) || []).length;
    return dollars && lb === rb && lp === rp;
}
//// End math helpers

function addCopyButtonsShared(root) {
    const scope = root instanceof Element ? root : document;
    const codeBlocks = scope.querySelectorAll('pre code');
    codeBlocks.forEach(codeBlock => {
        const pre = codeBlock.parentElement;
        if (!pre) return;
        if (pre.querySelector('.copy-button')) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'copy-button';
        button.innerText = 'Copy';
        pre.appendChild(button);
        button.addEventListener('click', () => {
            navigator.clipboard.writeText(codeBlock.innerText).then(() => {
                button.innerText = 'Copied!';
                setTimeout(() => button.innerText = 'Copy', 2000);
            }).catch(() => {
                button.innerText = 'Error';
                setTimeout(() => button.innerText = 'Copy', 2000);
            });
        });
    });
}

// Extract share token from URL
function getShareTokenFromUrl() {
    const path = window.location.pathname;
    const matches = path.match(/\/share\/([a-zA-Z0-9_-]+)/);
    return matches ? matches[1] : null;
}

// Determine whether a shared message should be rendered
function shouldDisplaySharedMessage(message) {
    if (!message) return false;
    normalizeSharedMessage(message);
    
    if (message.message_type === 'console') {
        return !isSharedTelemetryConsole(message);
    }
    
    return true;
}

function resetSharedStdoutState() {
    sharedStdoutMap.clear();
    lastSharedCodeId = null;
    sharedMessageCache.clear();
}

function normalizeSharedMessage(message) {
    if (!message) return message;
    if (!message.message_type && message.type) {
        message.message_type = message.type;
    }
    if (!message.message_format && message.format) {
        message.message_format = message.format;
    }
    if (!message.recipient && message.message_recipient) {
        message.recipient = message.message_recipient;
    }

    const recipient = (message.recipient || '').toLowerCase();
    if ((message.message_type === 'message' || message.message_type === 'text') &&
        SHARED_STD_STREAM_RECIPIENTS.includes(recipient)) {
        message.message_type = 'console';
        message.message_format = message.message_format || recipient || 'output';
    } else if (message.message_type === 'console' && !message.message_format) {
        message.message_format = recipient || 'output';
    }
    return message;
}

function shouldTrackSharedCode(message) {
    return Boolean(
        message &&
        message.message_type === 'code' &&
        message.role !== 'user' &&
        message.message_format !== 'html'
    );
}

function isSharedConsoleMessage(message) {
    return Boolean(
        message &&
        message.message_type === 'console' &&
        message.message_format !== 'active_line' &&
        !isSharedTelemetryConsole(message)
    );
}

function isSharedTelemetryConsole(message) {
    if (!message || message.message_type !== 'console') return false;
    if (message.message_format === 'active_line') return true;
    const content = typeof message.content === 'string' ? message.content.trim() : '';
    if (message.message_format === 'execution' && /^\d+(?:\/\d+)?$/.test(content)) {
        return true;
    }
    if (/^line\s+\d+$/i.test(content)) {
        return true;
    }
    return false;
}

function findSharedPreviousCodeId(referenceId) {
    if (!referenceId) return null;
    const chatDisplay = document.getElementById('chatDisplay');
    const messages = Array.from(chatDisplay.querySelectorAll('.message'));
    const index = messages.findIndex(el => el.getAttribute('data-id') === referenceId);
    if (index === -1) return null;
    for (let i = index - 1; i >= 0; i--) {
        const candidateId = messages[i].getAttribute('data-id');
        const data = sharedMessageCache.get(candidateId);
        if (data && shouldTrackSharedCode(data)) {
            return candidateId;
        }
    }
    return null;
}

function ensureSharedStdoutElements(codeId) {
    const messageElement = document.querySelector(`.message[data-id="${codeId}"]`);
    if (!messageElement) return {};
    const contentElement = messageElement.querySelector('.content');
    if (!contentElement) return {};

    let controls = messageElement.querySelector('.stdout-controls');
    let button = controls ? controls.querySelector('.stdout-button') : null;
    let panel = messageElement.querySelector('.stdout-panel');

    if (!controls) {
        controls = document.createElement('div');
        controls.className = 'stdout-controls stdout-hidden';
        button = document.createElement('button');
        button.className = 'stdout-button';
        button.type = 'button';
        button.textContent = 'Show Output';
        button.setAttribute('aria-expanded', 'false');
        button.disabled = true;
        button.addEventListener('click', () => toggleSharedStdoutPanel(codeId));
        controls.appendChild(button);
        contentElement.appendChild(controls);
    }

    if (!panel) {
        panel = document.createElement('div');
        panel.className = 'stdout-panel';
        panel.setAttribute('role', 'region');
        panel.setAttribute('aria-label', 'STDOUT and STDERR');
        panel.setAttribute('aria-hidden', 'true');
        contentElement.appendChild(panel);
    }

    return { messageElement, contentElement, controls, button, panel };
}

function updateSharedStdoutAvailability(codeId) {
    const { controls, button, panel } = ensureSharedStdoutElements(codeId);
    if (!controls || !button) return;
    const hasOutput = (sharedStdoutMap.get(codeId) || []).length > 0;
    controls.classList.toggle('stdout-hidden', !hasOutput);
    button.disabled = !hasOutput;
    if (!hasOutput) {
        button.textContent = 'Show Output';
        button.setAttribute('aria-expanded', 'false');
        if (panel) {
            panel.classList.remove('open');
            panel.setAttribute('aria-hidden', 'true');
        }
    }
}

function addSharedConsoleOutput(codeId, message) {
    if (!codeId) return;
    const text = typeof message.content === 'string' ? message.content : '';
    if (!text.trim()) return;
    if (!sharedStdoutMap.has(codeId)) {
        sharedStdoutMap.set(codeId, []);
    }
    sharedStdoutMap.get(codeId).push(text);
    updateSharedStdoutAvailability(codeId);
    const { panel } = ensureSharedStdoutElements(codeId);
    if (panel && panel.classList.contains('open')) {
        renderSharedStdoutPanel(codeId);
        panel.scrollTop = panel.scrollHeight;
    }
}

function renderSharedStdoutPanel(codeId) {
    const { panel } = ensureSharedStdoutElements(codeId);
    if (!panel) return;
    panel.innerHTML = '';
    const outputs = sharedStdoutMap.get(codeId) || [];
    if (outputs.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'stdout-empty';
        empty.textContent = 'No console output captured.';
        panel.appendChild(empty);
        return;
    }
    outputs.forEach(content => {
        const entry = document.createElement('div');
        entry.className = 'stdout-entry';
        const pre = document.createElement('pre');
        pre.classList.add('stdout-pre');
        const code = document.createElement('code');
        code.classList.add('stdout-code');
        code.textContent = content;
        pre.appendChild(code);
        entry.appendChild(pre);
        panel.appendChild(entry);
    });
    if (outputs.length > 0) {
        addCopyButtonsShared(panel);
    }
    if (typeof Prism !== 'undefined') {
        Prism.highlightAllUnder(panel);
    }
}

function toggleSharedStdoutPanel(codeId) {
    const { button, panel } = ensureSharedStdoutElements(codeId);
    if (!button || !panel || button.disabled) return;
    const isOpen = panel.classList.toggle('open');
    if (isOpen) {
        panel.setAttribute('aria-hidden', 'false');
        button.textContent = 'Hide Output';
        renderSharedStdoutPanel(codeId);
        panel.scrollTop = panel.scrollHeight;
        panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } else {
        panel.setAttribute('aria-hidden', 'true');
        button.textContent = 'Show Output';
    }
    button.setAttribute('aria-expanded', String(isOpen));
}

// Display message in chat (similar to conversation_ui.js but simplified for read-only)
function displayMessageInChat(message) {
    if (!shouldDisplaySharedMessage(message)) {
        return;
    }
    normalizeSharedMessage(message);
    
    const chatDisplay = document.getElementById('chatDisplay');
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.role}`;
    const messageId = message.id || generateId('msg');
    messageDiv.setAttribute('data-id', messageId);
    if (!message.id) {
        message.id = messageId;
    }
    sharedMessageCache.set(messageId, message);
    
    const contentElement = document.createElement('div');
    contentElement.classList.add('content');
    contentElement.setAttribute('data-type', message.message_type);
    
    // Handle different message types and formats similar to conversation_ui.js
    if (message.message_type === 'message') {
        const raw = message.content || '';
        const { text: shielded, store } = protectMath(raw);
        if (!hasBalancedMath(raw)) {
            contentElement.textContent = raw;
        } else {
            const parsedMarkdown = marked ? marked.parse(shielded) : shielded;
            contentElement.innerHTML = restoreMath(parsedMarkdown, store);
        }
    } else if (message.message_type === 'image') {
        if (message.message_format === 'base64.png') {
            contentElement.innerHTML = `<img src="data:image/png;base64,${message.content}" alt="Image">`;
        } else if (message.message_format === 'path') {
            contentElement.innerHTML = `<img src="${message.content}" alt="Image">`;
        } else {
            contentElement.innerHTML = `<img src="${message.content}" alt="Image">`;
        }
    } else if (message.message_type === 'code') {
        if (message.message_format === 'html') {
            contentElement.innerHTML = message.content;
        } else {
            const language = message.message_format || '';
            contentElement.innerHTML = `<pre><code class="language-${language}">${escapeHtml(message.content)}</code></pre>`;
        }
    } else if (message.message_type === 'console') {
        contentElement.innerHTML = '<pre><code></code></pre>';
        messageDiv.classList.add('console-output-message');
        contentElement.setAttribute('aria-hidden', 'true');
    } else if (message.message_type === 'file') {
        contentElement.innerHTML = `<div class="file-attachment">
            <span class="material-icons">attach_file</span>
            <span>File: ${message.content}</span>
        </div>`;
    } else {
        // Default handling for other types
        contentElement.innerHTML = message.content;
    }
    
    messageDiv.appendChild(contentElement);
    chatDisplay.appendChild(messageDiv);
    addCopyButtonsShared(contentElement);
    
    if (shouldTrackSharedCode(message)) {
        lastSharedCodeId = messageId;
        ensureSharedStdoutElements(messageId);
    } else if (isSharedConsoleMessage(message)) {
        messageDiv.classList.add('console-output-message');
        contentElement.setAttribute('aria-hidden', 'true');
        let targetCodeId = lastSharedCodeId;
        if (!targetCodeId) {
            targetCodeId = findSharedPreviousCodeId(messageId);
        }
        if (targetCodeId) {
            ensureSharedStdoutElements(targetCodeId);
            addSharedConsoleOutput(targetCodeId, message);
            lastSharedCodeId = targetCodeId;
        }
    }
    
    // Apply syntax highlighting if there's code
    if (typeof Prism !== 'undefined') {
        Prism.highlightAllUnder(messageDiv);
    }
    
    // Re-render MathJax if available
    if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
        MathJax.typesetPromise([messageDiv]);
    }
}

// Helper function to generate IDs
function generateId(id_type) {
    return id_type + '-' + Math.random().toString(36).substr(2, 9);
}

// Helper function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Format date for display
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
}

// Show error state
function showError(message) {
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('chatContainer').style.display = 'none';
    document.getElementById('emptyState').style.display = 'none';
    
    const errorState = document.getElementById('errorState');
    const errorMessage = document.getElementById('errorMessage');
    errorMessage.textContent = message;
    errorState.style.display = 'block';
}

// Show empty conversation state
function showEmpty() {
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('chatContainer').style.display = 'none';
    document.getElementById('errorState').style.display = 'none';
    document.getElementById('emptyState').style.display = 'block';
}

// Show chat content
function showChat() {
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('errorState').style.display = 'none';
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('chatContainer').style.display = 'block';
}

// Update conversation info in header
function updateConversationInfo(conversation) {
    const conversationInfo = document.getElementById('conversationInfo');
    if (!conversationInfo) return;
    
    const createdDate = formatDate(conversation.created_at);
    
    conversationInfo.innerHTML = `
        <span><strong>Created:</strong> ${createdDate} UTC</span>
    `;
}

// Load shared conversation
async function loadSharedConversation() {
    const shareToken = getShareTokenFromUrl();
    
    if (!shareToken) {
        showError('Invalid share link - no token found');
        return;
    }
    
    try {
        const apiBaseUrl = window.API_BASE_URL || 'http://localhost:8002';
        const response = await fetch(`${apiBaseUrl}/conversations/shared/${shareToken}`);
        
        if (!response.ok) {
            if (response.status === 404) {
                showError('This shared conversation could not be found or is no longer available');
            } else {
                showError('Failed to load shared conversation');
            }
            return;
        }
        
        const conversation = await response.json();
        
        // Update page title
        if (conversation.title) {
            document.title = `${conversation.title} - Shared Conversation - IDEA`;
        }
        
        // Update conversation info
        updateConversationInfo(conversation);
        
        // Clear chat display
        const chatDisplay = document.getElementById('chatDisplay');
        chatDisplay.innerHTML = '';
        resetSharedStdoutState();
        
        // Check if conversation has messages
        if (!conversation.messages || conversation.messages.length === 0) {
            showEmpty();
            return;
        }
        
        // Display messages
        conversation.messages
            .filter(shouldDisplaySharedMessage)
            .forEach(message => {
                displayMessageInChat(message);
            });
        
        // Show chat container
        showChat();
        
        // Scroll to top after loading
        chatDisplay.scrollTop = 0;
        
    } catch (error) {
        console.error('Error loading shared conversation:', error);
        showError('Failed to load shared conversation - please check your connection and try again');
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    loadSharedConversation();
});
