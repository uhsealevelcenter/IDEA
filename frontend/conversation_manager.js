/**
 * Conversation Management Module
 * Handles conversation persistence, message storage, and conversation history
 */

const MESSAGE_TYPES = {
    MESSAGE: 'message',
    CODE: 'code',
    IMAGE: 'image',
    CONSOLE: 'console',
    FILE: 'file',
    CONFIRMATION: 'confirmation'
};

const MESSAGE_ROLES = {
    USER: 'user',
    ASSISTANT: 'assistant',
    COMPUTER: 'computer'
};

class ConversationManager {
    constructor(apiBaseUrl, sessionId) {
        this.apiBaseUrl = apiBaseUrl || window.API_BASE_URL || 'http://localhost:8002';
        this.sessionId = sessionId || this.getOrCreateSessionId();
        this.currentConversationId = null;
        this.conversations = [];
        this.currentMessages = [];
        
        // Initialize conversation management
        this.init();
    }
    
    /**
     * Get or create a session ID for this browser session
     */
    getOrCreateSessionId() {
        let sessionId = sessionStorage.getItem('idea_session_id');
        if (!sessionId) {
            sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            sessionStorage.setItem('idea_session_id', sessionId);
        }
        return sessionId;
    }
    
    /**
     * Initialize the conversation manager
     */
    async init() {
        try {
            await this.loadConversations();
        } catch (error) {
            console.error('Failed to initialize conversation manager:', error);
        }
    }
    
    /**
     * Load all conversations for the current session
     */
    async loadConversations() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/conversations/?session_id=${this.sessionId}`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const data = await response.json();
            this.conversations = data.data || [];
            this.notifyConversationListeners('conversations_loaded', this.conversations);
            return this.conversations;
        } catch (error) {
            console.error('Error loading conversations:', error);
            throw error;
        }
    }
    
    /**
     * Create a new conversation
     */
    async createConversation(title = null) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/conversations/?session_id=${this.sessionId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    title: title
                }),
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const conversation = await response.json();
            this.conversations.unshift(conversation);
            this.currentConversationId = conversation.id;
            this.currentMessages = [];
            
            this.notifyConversationListeners('conversation_created', conversation);
            this.notifyConversationListeners('conversation_changed', conversation);
            return conversation;
        } catch (error) {
            console.error('Error creating conversation:', error);
            throw error;
        }
    }
    
    /**
     * Get a specific conversation with its messages
     */
    async loadConversation(conversationId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/conversations/${conversationId}?session_id=${this.sessionId}`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const conversation = await response.json();
            this.currentConversationId = conversationId;
            this.currentMessages = conversation.messages || [];
            
            this.notifyConversationListeners('conversation_loaded', conversation);
            this.notifyConversationListeners('messages_updated', this.currentMessages);
            return conversation;
        } catch (error) {
            console.error('Error loading conversation:', error);
            throw error;
        }
    }
    
    /**
     * Add a message to the current conversation
     */
    async addMessage(role, content, messageType = MESSAGE_TYPES.MESSAGE, messageFormat = null, recipient = null) {
        if (!this.currentConversationId) {
            // Create a new conversation if none exists
            await this.createConversation();
        }
        
        try {
            const messageData = {
                role: role,
                content: content,
                message_type: messageType,
                message_format: messageFormat,
                recipient: recipient,
                conversation_id: this.currentConversationId
            };
            
            const response = await fetch(`${this.apiBaseUrl}/conversations/${this.currentConversationId}/messages?session_id=${this.sessionId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(messageData),
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const message = await response.json();
            this.currentMessages.push(message);
            
            // Update the conversation's updated_at timestamp in our local list
            const conversationIndex = this.conversations.findIndex(c => c.id === this.currentConversationId);
            if (conversationIndex !== -1) {
                this.conversations[conversationIndex].updated_at = message.created_at;
                // Move to top of list
                const conversation = this.conversations.splice(conversationIndex, 1)[0];
                this.conversations.unshift(conversation);
            }
            
            this.notifyConversationListeners('message_added', message);
            this.notifyConversationListeners('messages_updated', this.currentMessages);
            return message;
        } catch (error) {
            console.error('Error adding message:', error);
            throw error;
        }
    }
    
    /**
     * Delete a conversation
     */
    async deleteConversation(conversationId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/conversations/${conversationId}?session_id=${this.sessionId}`, {
                method: 'DELETE',
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            // Remove from local list
            this.conversations = this.conversations.filter(c => c.id !== conversationId);
            
            // If this was the current conversation, clear it
            if (this.currentConversationId === conversationId) {
                this.currentConversationId = null;
                this.currentMessages = [];
                this.notifyConversationListeners('conversation_changed', null);
                this.notifyConversationListeners('messages_updated', []);
            }
            
            this.notifyConversationListeners('conversation_deleted', conversationId);
            this.notifyConversationListeners('conversations_loaded', this.conversations);
            
        } catch (error) {
            console.error('Error deleting conversation:', error);
            throw error;
        }
    }
    
    /**
     * Update conversation (title, favorite status)
     */
    async updateConversation(conversationId, updates) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/conversations/${conversationId}?session_id=${this.sessionId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(updates),
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const updatedConversation = await response.json();
            
            // Update in local list
            const index = this.conversations.findIndex(c => c.id === conversationId);
            if (index !== -1) {
                this.conversations[index] = updatedConversation;
            }
            
            this.notifyConversationListeners('conversation_updated', updatedConversation);
            this.notifyConversationListeners('conversations_loaded', this.conversations);
            return updatedConversation;
        } catch (error) {
            console.error('Error updating conversation:', error);
            throw error;
        }
    }
    
    /**
     * Toggle favorite status of a conversation
     */
    async toggleFavorite(conversationId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/conversations/${conversationId}/favorite?session_id=${this.sessionId}`, {
                method: 'POST',
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const updatedConversation = await response.json();
            
            // Update in local list
            const index = this.conversations.findIndex(c => c.id === conversationId);
            if (index !== -1) {
                this.conversations[index] = updatedConversation;
            }
            
            this.notifyConversationListeners('conversation_updated', updatedConversation);
            this.notifyConversationListeners('conversations_loaded', this.conversations);
            return updatedConversation;
        } catch (error) {
            console.error('Error toggling favorite:', error);
            throw error;
        }
    }
    
    /**
     * Create a share link for a conversation
     */
    async createShareLink(conversationId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/conversations/${conversationId}/share?session_id=${this.sessionId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({}),
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const shareData = await response.json();
            
            // Update conversation in local list to reflect shared status
            const index = this.conversations.findIndex(c => c.id === conversationId);
            if (index !== -1) {
                this.conversations[index].is_shared = true;
            }
            
            this.notifyConversationListeners('share_created', shareData);
            return shareData;
        } catch (error) {
            console.error('Error creating share link:', error);
            throw error;
        }
    }
    
    /**
     * Start a new conversation (clear current state)
     */
    startNewConversation() {
        this.currentConversationId = null;
        this.currentMessages = [];
        this.notifyConversationListeners('conversation_changed', null);
        this.notifyConversationListeners('messages_updated', []);
    }
    
    /**
     * Get current conversation ID
     */
    getCurrentConversationId() {
        return this.currentConversationId;
    }
    
    /**
     * Get current messages
     */
    getCurrentMessages() {
        return this.currentMessages;
    }
    
    /**
     * Get all conversations
     */
    getAllConversations() {
        return this.conversations;
    }
    
    /**
     * Event listener management
     */
    listeners = {};
    
    addEventListener(event, callback) {
        if (!this.listeners[event]) {
            this.listeners[event] = [];
        }
        this.listeners[event].push(callback);
    }
    
    removeEventListener(event, callback) {
        if (this.listeners[event]) {
            this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
        }
    }
    
    notifyConversationListeners(event, data) {
        if (this.listeners[event]) {
            this.listeners[event].forEach(callback => {
                try {
                    callback(data);
                } catch (error) {
                    console.error('Error in conversation event listener:', error);
                }
            });
        }
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ConversationManager, MESSAGE_TYPES, MESSAGE_ROLES };
} else {
    window.ConversationManager = ConversationManager;
    window.MESSAGE_TYPES = MESSAGE_TYPES;
    window.MESSAGE_ROLES = MESSAGE_ROLES;
}