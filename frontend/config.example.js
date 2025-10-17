const config = {
    environment: 'local', // Change to 'production' for production environment
    
    // API endpoints
    endpoints: {
        local: {
            chat: 'http://localhost/api/chat',
            history: 'http://localhost/api/history',
            clear: 'http://localhost/api/clear',
            upload: 'http://localhost/api/upload',
            files: 'http://localhost/api/files',
            transcribe: 'http://localhost/api/transcribe',
            login: 'http://localhost/api/login',
            logout: 'http://localhost/api/logout',
            changePassword: 'http://localhost/api/users/change-password',
            verify: 'http://localhost/api/auth/verify',
            userProfile: 'http://localhost/api/users/me',
            users: 'http://localhost/api/users',
            prompts: 'http://localhost/api/prompts',
            setActivePrompt: 'http://localhost/api/prompts/set-active',
            knowledgeBase: 'http://localhost/api/knowledge-base/papers',
            knowledgeBaseUpload: 'http://localhost/api/knowledge-base/papers/upload',
            knowledgeBaseStats: 'http://localhost/api/knowledge-base/stats',
            conversations: 'http://localhost/api/conversations',
            conversationMessages: 'http://localhost/api/conversations',
            conversationShare: 'http://localhost/conversations',
            loadConversation: 'http://localhost/api/load-conversation'
        },
        production: {
            chat: 'https://<your-domain>/chat',
            history: 'https://<your-domain>/history',
            clear: 'https://<your-domain>/clear',
            upload: 'https://<your-domain>/upload',
            files: 'https://<your-domain>/files',
            transcribe: 'https://<your-domain>/transcribe',
            login: 'https://<your-domain>/api/login',
            logout: 'https://<your-domain>/api/logout',
            verify: 'https://<your-domain>/api/auth/verify',
            userProfile: 'https://<your-domain>/api/users/me',
            users: 'https://<your-domain>/api/users',
            changePassword: 'https://<your-domain>/api/users/change-password',
            prompts: 'https://<your-domain>/api/prompts',
            setActivePrompt: 'https://<your-domain>/api/prompts/set-active',
            knowledgeBase: 'https://<your-domain>/api/knowledge-base/papers',
            knowledgeBaseUpload: 'https://<your-domain>/api/knowledge-base/papers/upload',
            knowledgeBaseStats: 'https://<your-domain>/api/knowledge-base/stats',
            conversations: 'https://<your-domain>/conversations',
            conversationMessages: 'https://<your-domain>/conversations',
            conversationShare: 'https://<your-domain>/conversations',
            loadConversation: 'https://<your-domain>/api/load-conversation'
        }
    },

    // Get the current environment's endpoints
    getEndpoints() {
        return this.endpoints[this.environment];
    }
};

// Set global API_BASE_URL for ConversationManager
window.API_BASE_URL = (() => {
    const endpoints = config.endpoints[config.environment];
    if (endpoints.conversations) {
        const url = new URL(endpoints.conversations);
        return `${url.protocol}//${url.host}`;
    }
    return 'http://localhost:8002';
})();
