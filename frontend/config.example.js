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
            transcribe: 'http://localhost/api/transcribe'
        },
        production: {
            chat: 'https://<your-domain>/chat',
            history: 'https://<your-domain>/history',
            clear: 'https://<your-domain>/clear',
            upload: 'https://<your-domain>/upload',
            files: 'https://<your-domain>/files',
            transcribe: 'https://<your-domain>/transcribe' 
        }
    },

    // Get the current environment's endpoints
    getEndpoints() {
        return this.endpoints[this.environment];
    }
};
