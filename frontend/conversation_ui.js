/**
 * Conversation UI Management
 * Handles the UI for conversation history, loading, and management
 */

let isShowingFavorites = false;
// conversationManager is declared in assistant.js

// Initialize conversation UI when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    initializeConversationUI();
});

function initializeConversationUI() {
    // Initialize conversation manager
    conversationManager = new ConversationManager();
    
    // Set up event listeners for conversation UI
    setupConversationEventListeners();
    
    // Set up conversation manager listeners
    setupConversationManagerListeners();
}

function setupConversationEventListeners() {
    // Open conversations modal
    document.getElementById('conversationsButton').addEventListener('click', openConversationsModal);
    document.getElementById('conversationsButtonMobile').addEventListener('click', openConversationsModal);
    
    // Close conversations modal
    document.getElementById('closeConversationsModal').addEventListener('click', closeConversationsModal);
    
    // Search conversations
    document.getElementById('conversationSearch').addEventListener('input', debounce(searchConversations, 300));
    
    // Filter buttons
    document.getElementById('showAllConversations').addEventListener('click', () => {
        isShowingFavorites = false;
        updateFilterButtons();
        displayConversations();
    });
    
    document.getElementById('showFavoriteConversations').addEventListener('click', () => {
        isShowingFavorites = true;
        updateFilterButtons();
        displayConversations();
    });
    
    // Refresh conversations
    document.getElementById('refreshConversations').addEventListener('click', async () => {
        await conversationManager.loadConversations();
        displayConversations();
    });
}

function setupConversationManagerListeners() {
    // Listen for conversation manager events
    conversationManager.addEventListener('conversations_loaded', displayConversations);
    conversationManager.addEventListener('conversation_created', () => {
        // Refresh the conversation list when a new conversation is created
        displayConversations();
    });
    conversationManager.addEventListener('conversation_updated', displayConversations);
    conversationManager.addEventListener('conversation_deleted', displayConversations);
}

function openConversationsModal() {
    document.getElementById('conversationsModal').style.display = 'block';
    // Load conversations when modal opens
    displayConversations();
}

function closeConversationsModal() {
    document.getElementById('conversationsModal').style.display = 'none';
}

function updateFilterButtons() {
    const allBtn = document.getElementById('showAllConversations');
    const favBtn = document.getElementById('showFavoriteConversations');
    
    if (isShowingFavorites) {
        allBtn.classList.remove('active');
        favBtn.classList.add('active');
    } else {
        allBtn.classList.add('active');
        favBtn.classList.remove('active');
    }
}

function displayConversations() {
    const conversationsList = document.getElementById('conversationsList');
    const conversations = conversationManager.getAllConversations();
    const searchTerm = document.getElementById('conversationSearch').value.toLowerCase();
    
    // Filter conversations
    let filteredConversations = conversations;
    
    if (isShowingFavorites) {
        filteredConversations = conversations.filter(conv => conv.is_favorite);
    }
    
    if (searchTerm) {
        filteredConversations = filteredConversations.filter(conv => 
            (conv.title && conv.title.toLowerCase().includes(searchTerm))
        );
    }
    
    if (filteredConversations.length === 0) {
        conversationsList.innerHTML = `
            <div class="empty-state">
                <span class="material-icons">chat_bubble_outline</span>
                <p>${isShowingFavorites ? 'No favorite conversations found' : 'No conversations found'}</p>
                <p class="empty-state-subtitle">Start a new conversation to see it here</p>
            </div>
        `;
        return;
    }
    
    const conversationsHTML = filteredConversations.map(conversation => createConversationItem(conversation)).join('');
    conversationsList.innerHTML = conversationsHTML;
    
    // Add event listeners to conversation items
    filteredConversations.forEach(conversation => {
        const conversationElement = document.getElementById(`conversation-${conversation.id}`);
        if (conversationElement) {
            // Load conversation on click
            conversationElement.addEventListener('click', (e) => {
                if (!e.target.closest('.conversation-actions')) {
                    loadConversation(conversation.id);
                }
            });
            
            // Favorite button
            const favoriteBtn = conversationElement.querySelector('.favorite-btn');
            if (favoriteBtn) {
                favoriteBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    toggleFavorite(conversation.id);
                });
            }
            
            // Delete button
            const deleteBtn = conversationElement.querySelector('.delete-btn');
            if (deleteBtn) {
                deleteBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    deleteConversation(conversation.id, conversation.title);
                });
            }
            
            // Share button
            const shareBtn = conversationElement.querySelector('.share-btn');
            if (shareBtn) {
                shareBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    shareConversation(conversation.id);
                });
            }
        }
    });
}

function createConversationItem(conversation) {
    const date = new Date(conversation.created_at).toLocaleDateString();
    const time = new Date(conversation.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    const title = conversation.title || 'Untitled Conversation';
    const isCurrentConversation = conversation.id === conversationManager.getCurrentConversationId();
    
    return `
        <div class="conversation-item ${isCurrentConversation ? 'current' : ''}" id="conversation-${conversation.id}">
            <div class="conversation-content">
                <div class="conversation-header">
                    <h4 class="conversation-title">${escapeHtml(title)}</h4>
                    <div class="conversation-meta">
                        <span class="conversation-date">${date} ${time}</span>
                        ${conversation.is_favorite ? '<span class="material-icons favorite-indicator">star</span>' : ''}
                        ${conversation.is_shared ? '<span class="material-icons shared-indicator">share</span>' : ''}
                    </div>
                </div>
            </div>
            <div class="conversation-actions">
                <button class="action-btn favorite-btn ${conversation.is_favorite ? 'active' : ''}" 
                        title="${conversation.is_favorite ? 'Remove from favorites' : 'Add to favorites'}">
                    <span class="material-icons">${conversation.is_favorite ? 'star' : 'star_border'}</span>
                </button>
                <button class="action-btn share-btn" title="Share conversation">
                    <span class="material-icons">share</span>
                </button>
                <button class="action-btn delete-btn" title="Delete conversation">
                    <span class="material-icons">delete</span>
                </button>
            </div>
        </div>
    `;
}

async function loadConversation(conversationId) {
    try {
        const conversation = await conversationManager.loadConversation(conversationId);
        
        // Close the modal
        closeConversationsModal();
        
        const chatDisplay = document.getElementById('chatDisplay');
        const loadedMessages = conversationManager.getCurrentMessages() || [];

        if (typeof window.hydrateChatWithMessages === 'function') {
            window.hydrateChatWithMessages(loadedMessages, { persist: false });
        } else {
            chatDisplay.innerHTML = '';
            if (typeof window.resetStdoutState === 'function') {
                window.resetStdoutState();
            }
            loadedMessages.forEach(message => {
                displayMessageInChat(message);
            });
        }
        
        // Load conversation context into backend interpreter
        await loadConversationIntoInterpreter(loadedMessages);
        
        // Update the current conversation indicator
        displayConversations();
        
        // Notify that conversation was loaded
        showNotification(`Loaded conversation: ${conversation.title || 'Untitled'}`, 'success');
        
    } catch (error) {
        console.error('Error loading conversation:', error);
        showNotification('Failed to load conversation', 'error');
    }
}

async function toggleFavorite(conversationId) {
    try {
        await conversationManager.toggleFavorite(conversationId);
        displayConversations();
        showNotification('Conversation updated', 'success');
    } catch (error) {
        console.error('Error toggling favorite:', error);
        showNotification('Failed to update conversation', 'error');
    }
}

async function deleteConversation(conversationId, title) {
    const confirmed = confirm(`Are you sure you want to delete "${title || 'this conversation'}"? This action cannot be undone.`);
    
    if (confirmed) {
        try {
            await conversationManager.deleteConversation(conversationId);
            displayConversations();
            showNotification('Conversation deleted', 'success');
        } catch (error) {
            console.error('Error deleting conversation:', error);
            showNotification('Failed to delete conversation', 'error');
        }
    }
}

async function shareConversation(conversationId) {
    try {
        const shareData = await conversationManager.createShareLink(conversationId);
        
        // Create full URL
        const fullShareUrl = `${window.location.origin}${shareData.share_url}`;
        
        // Try to copy to clipboard
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(fullShareUrl);
            showNotification('Share link copied to clipboard!', 'success');
        } else {
            // Fallback: show the URL in a prompt
            prompt('Copy this link to share the conversation:', fullShareUrl);
        }
        
        displayConversations();
        
    } catch (error) {
        console.error('Error creating share link:', error);
        showNotification('Failed to create share link', 'error');
    }
}

function displayMessageInChat(message) {
    const chatDisplay = document.getElementById('chatDisplay');
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.role}`;
    messageDiv.setAttribute('data-id', message.id || generateId('msg'));
    
    const contentElement = document.createElement('div');
    contentElement.classList.add('content');
    contentElement.setAttribute('data-type', message.message_type);
    
    // Handle different message types and formats similar to updateMessageContent in assistant.js
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
        contentElement.style.display = 'none'; // Hide console output by default
    } else if (message.message_type === 'file') {
        contentElement.innerHTML = `<a href="${message.content}" download>Download File</a>`;
    } else {
        // Default handling for other types
        contentElement.innerHTML = message.content;
    }
    
    messageDiv.appendChild(contentElement);
    chatDisplay.appendChild(messageDiv);
    
    // Scroll to bottom
    chatDisplay.scrollTop = chatDisplay.scrollHeight;
    
    // Apply syntax highlighting if there's code
    if (typeof Prism !== 'undefined') {
        Prism.highlightAllUnder(messageDiv);
    }
    
    // Re-render MathJax if available
    if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
        MathJax.typesetPromise([messageDiv]);
    }
}

// Helper function to generate IDs (matching the one in assistant.js)
function generateId(id_type) {
    return id_type + '-' + Math.random().toString(36).substr(2, 9);
}

function searchConversations() {
    displayConversations();
}

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = `
        <span class="material-icons">${getNotificationIcon(type)}</span>
        <span>${message}</span>
    `;
    
    // Add to page
    document.body.appendChild(notification);
    
    // Show notification
    setTimeout(() => notification.classList.add('show'), 100);
    
    // Hide and remove after 3 seconds
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => document.body.removeChild(notification), 300);
    }, 3000);
}

function getNotificationIcon(type) {
    switch (type) {
        case 'success': return 'check_circle';
        case 'error': return 'error';
        case 'warning': return 'warning';
        default: return 'info';
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

async function loadConversationIntoInterpreter(messages) {
    try {
        const response = await fetch(config.getEndpoints().loadConversation, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Session-Id': sessionId,
                ...getAuthHeaders()
            },
            body: JSON.stringify({
                messages: messages
            })
        });
        
        if (!response.ok) {
            throw new Error(`Failed to load conversation into interpreter: ${response.status}`);
        }
        
        const result = await response.json();
        console.log(`Loaded ${result.message_count} messages into interpreter context`);
        
    } catch (error) {
        console.error('Error loading conversation into interpreter:', error);
        throw error;
    }
}

// Export for use in other modules
window.conversationUI = {
    openConversationsModal,
    closeConversationsModal,
    displayMessageInChat,
    showNotification
};
