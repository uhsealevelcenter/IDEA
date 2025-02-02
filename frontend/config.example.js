const config = {
    environment: 'local', // Change to 'production' for production environment
    
    // API endpoints
    endpoints: {
        local: {
            chat: 'http://localhost/api/chat',
            history: 'http://localhost/api/history',
            clear: 'http://localhost/api/clear'
        },
        production: {
            chat: 'https://nemo-dev1.uhslc.org/chat',
            history: 'https://nemo-dev1.uhslc.org/history',
            clear: 'https://nemo-dev1.uhslc.org/clear'
        }
    },

    // Get the current environment's endpoints
    getEndpoints() {
        return this.endpoints[this.environment];
    }
};
