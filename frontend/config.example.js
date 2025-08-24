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
            verify: 'http://localhost/api/auth/verify',
            changePassword: 'http://localhost/api/users/change-password',
            prompts: 'http://localhost/api/prompts',
            setActivePrompt: 'http://localhost/api/prompts/set-active',
            knowledgeBase: 'http://localhost/api/knowledge-base/papers',
            knowledgeBaseUpload: 'http://localhost/api/knowledge-base/papers/upload',
            knowledgeBaseStats: 'http://localhost/api/knowledge-base/stats'
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
            changePassword: 'https://<your-domain>/api/users/change-password',
            prompts: 'https://<your-domain>/api/prompts',
            setActivePrompt: 'https://<your-domain>/api/prompts/set-active',
            knowledgeBase: 'https://<your-domain>/api/knowledge-base/papers',
            knowledgeBaseUpload: 'https://<your-domain>/api/knowledge-base/papers/upload',
            knowledgeBaseStats: 'https://<your-domain>/api/knowledge-base/stats'
        }
    },

    // Get the current environment's endpoints
    getEndpoints() {
        return this.endpoints[this.environment];
    }
};
