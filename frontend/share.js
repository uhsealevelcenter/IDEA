/**
 * Shared Conversation Viewer
 * Handles loading and displaying shared conversations in read-only mode
 */

// Extract share token from URL
function getShareTokenFromUrl() {
    const path = window.location.pathname;
    const matches = path.match(/\/share\/([a-zA-Z0-9_-]+)/);
    return matches ? matches[1] : null;
}

// Display message in chat (similar to conversation_ui.js but simplified for read-only)
function displayMessageInChat(message) {
    const chatDisplay = document.getElementById('chatDisplay');
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.role}`;
    messageDiv.setAttribute('data-id', message.id || generateId('msg'));
    
    const contentElement = document.createElement('div');
    contentElement.classList.add('content');
    contentElement.setAttribute('data-type', message.message_type);
    
    // Handle different message types and formats similar to conversation_ui.js
    if (message.message_type === 'message') {
        // Handle markdown content
        contentElement.innerHTML = marked ? marked.parse(message.content) : message.content;
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
        contentElement.innerHTML = `<pre>${escapeHtml(message.content)}</pre>`;
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
    
    const title = conversation.title || 'Untitled Conversation';
    const createdDate = formatDate(conversation.created_at);
    const messageCount = conversation.messages ? conversation.messages.length : 0;
    
    conversationInfo.innerHTML = `
        <div class="shared-conversation-title">${escapeHtml(title)}</div>
        <div>
            <span><strong>Created:</strong> ${createdDate}</span>
            <span><strong>Messages:</strong> ${messageCount}</span>
        </div>
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
        
        // Check if conversation has messages
        if (!conversation.messages || conversation.messages.length === 0) {
            showEmpty();
            return;
        }
        
        // Display messages
        conversation.messages.forEach(message => {
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