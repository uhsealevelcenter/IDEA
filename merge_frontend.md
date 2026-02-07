# Merging Frontend into Backend - Implementation Guide

## Overview

This document outlines the changes needed to serve all frontend files through the Python backend application under the `/IDEA/` path prefix. This consolidation makes load balancing easier by routing all traffic through a single backend service.

## Goals

- Serve frontend files (HTML, CSS, JS) from the Python backend
- Route all endpoints under `/IDEA/` prefix:
  - Frontend: `/IDEA/index.html`, `/IDEA/login.html`, etc.
  - API: `/IDEA/api/...`
  - Idea-API: `/IDEA/idea-api/...`
  - Conversations: `/IDEA/conversations/...`
  - Share: `/IDEA/share/...`
  - Static: `/IDEA/static/...`

## Changes Required

### 1. Python Application (`app.py`)

Add frontend static file mounting after line 164:

```python
# Serve frontend files under /IDEA/ path
app.mount('/IDEA', StaticFiles(directory='frontend', html=True), name='frontend')
```

**Location:** After the existing `/assets` mount (around line 164)

**Note:** With `root_path="/idea-api"`, this mount will be accessible at `/idea-api/IDEA/` from FastAPI's perspective, but nginx will handle the routing to make it appear as `/IDEA/`.

### 2. Nginx Configuration (`nginx.conf`)

Replace the entire nginx configuration with:

```nginx
events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    client_max_body_size 30M;

    upstream backend {
        server web:8001;
    }

    server {
        listen 80;
        server_name localhost 0.0.0.0;

        # Frontend static files served by backend under /IDEA/
        location /IDEA/ {
            proxy_pass http://backend/idea-api/IDEA/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Backend API under /IDEA/api/ (strips /IDEA/api/ prefix)
        location /IDEA/api/ {
            client_max_body_size 30M;
            proxy_pass http://backend/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # WebSocket support
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";

            # Disable buffering for EventSource
            proxy_buffering off;
            proxy_cache off;
            proxy_set_header Connection '';
            chunked_transfer_encoding off;
        }

        # Backend API under /IDEA/idea-api/ (strips /IDEA prefix)
        location /IDEA/idea-api/ {
            client_max_body_size 30M;
            proxy_pass http://backend/idea-api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";

            proxy_buffering off;
            proxy_cache off;
            proxy_set_header Connection '';
            chunked_transfer_encoding off;
        }

        # Static files under /IDEA/static/
        location /IDEA/static/ {
            proxy_pass http://backend/idea-api/static/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Conversations under /IDEA/conversations/
        location /IDEA/conversations/ {
            client_max_body_size 30M;
            proxy_pass http://backend/conversations/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Share pages under /IDEA/share/
        location /IDEA/share/ {
            proxy_pass http://backend/share/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Optional: Redirect root to /IDEA/
        location = / {
            return 301 /IDEA/;
        }
    }
}
```

**Key Changes:**
- Removed `upstream frontend` block
- Removed old `/` location that proxied to frontend service
- Removed old `/api/`, `/idea-api/`, `/static/`, `/conversations/`, `/share/` locations
- Added all routes under `/IDEA/` prefix
- `/IDEA/api/` strips the prefix and proxies to backend root
- `/IDEA/idea-api/` strips `/IDEA` and proxies to `/idea-api/`

### 3. Frontend Configuration (`frontend/config.example.js`)

Update all endpoint URLs to include `/IDEA/` prefix:

```javascript
const config = {
    environment: 'local', // Change to 'production' for production environment
    
    endpoints: {
        local: {
            chat: 'http://localhost/IDEA/api/chat',
            history: 'http://localhost/IDEA/api/history',
            clear: 'http://localhost/IDEA/api/clear',
            interrupt: 'http://localhost/IDEA/api/interrupt',
            upload: 'http://localhost/IDEA/api/upload',
            files: 'http://localhost/IDEA/api/files',
            transcribe: 'http://localhost/IDEA/api/transcribe',
            login: 'http://localhost/IDEA/api/login',
            logout: 'http://localhost/IDEA/api/logout',
            changePassword: 'http://localhost/IDEA/api/users/change-password',
            verify: 'http://localhost/IDEA/api/auth/verify',
            userProfile: 'http://localhost/IDEA/api/users/me',
            users: 'http://localhost/IDEA/api/users',
            prompts: 'http://localhost/IDEA/api/prompts',
            setActivePrompt: 'http://localhost/IDEA/api/prompts/set-active',
            knowledgeBase: 'http://localhost/IDEA/idea-api/knowledge-base/papers',
            knowledgeBaseUpload: 'http://localhost/IDEA/idea-api/knowledge-base/papers/upload',
            knowledgeBaseStats: 'http://localhost/IDEA/idea-api/knowledge-base/stats',
            conversations: 'http://localhost/IDEA/conversations',
            conversationMessages: 'http://localhost/IDEA/conversations',
            conversationShare: 'http://localhost/IDEA/conversations',
            loadConversation: 'http://localhost/IDEA/api/load-conversation'
        },
        production: {
            chat: 'https://<your-domain>/IDEA/api/chat',
            history: 'https://<your-domain>/IDEA/api/history',
            clear: 'https://<your-domain>/IDEA/api/clear',
            interrupt: 'https://<your-domain>/IDEA/api/interrupt',
            upload: 'https://<your-domain>/IDEA/api/upload',
            files: 'https://<your-domain>/IDEA/api/files',
            transcribe: 'https://<your-domain>/IDEA/api/transcribe',
            login: 'https://<your-domain>/IDEA/api/login',
            logout: 'https://<your-domain>/IDEA/api/logout',
            verify: 'https://<your-domain>/IDEA/api/auth/verify',
            userProfile: 'https://<your-domain>/IDEA/api/users/me',
            users: 'https://<your-domain>/IDEA/api/users',
            changePassword: 'https://<your-domain>/IDEA/api/users/change-password',
            prompts: 'https://<your-domain>/IDEA/api/prompts',
            setActivePrompt: 'https://<your-domain>/IDEA/api/prompts/set-active',
            knowledgeBase: 'https://<your-domain>/IDEA/idea-api/knowledge-base/papers',
            knowledgeBaseUpload: 'https://<your-domain>/IDEA/idea-api/knowledge-base/papers/upload',
            knowledgeBaseStats: 'https://<your-domain>/IDEA/idea-api/knowledge-base/stats',
            conversations: 'https://<your-domain>/IDEA/conversations',
            conversationMessages: 'https://<your-domain>/IDEA/conversations',
            conversationShare: 'https://<your-domain>/IDEA/conversations',
            loadConversation: 'https://<your-domain>/IDEA/api/load-conversation'
        }
    },

    getEndpoints() {
        return this.endpoints[this.environment];
    }
};

window.API_BASE_URL = (() => {
    const endpoints = config.endpoints[config.environment];
    if (endpoints.conversations) {
        const url = new URL(endpoints.conversations);
        return `${url.protocol}//${url.host}`;
    }
    return 'http://localhost:8002';
})();
```

**Note:** Users will need to copy `config.example.js` to `config.js` and update it with the new paths.

### 4. Docker Compose Files

#### `docker-compose.override.yml` (Development)

Remove the `frontend` service block (lines 30-41) and remove `frontend` from nginx's `depends_on`:

```yaml
version: '3.8'

services:
  web:
    # ... existing config ...
  
  db:
    # ... existing config ...
  
  # Remove entire frontend service block
  
  nginx:
    image: nginx:alpine
    ports:
      - "0.0.0.0:80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./frontend:/app/frontend:ro,delegated
      - ./static:/static:ro
    depends_on:
      - web
      # Remove frontend from depends_on
      
  redis:
    # ... existing config ...
```

#### `docker-compose.yml` (Production)

No changes needed - production compose file doesn't include frontend service.

### 5. Share Route Links (Optional)

Check `conversation_routes.py` around line 338 where share paths are generated. Update if needed to include `/IDEA/` prefix in generated share URLs:

```python
share_path = f"{root_path}/IDEA/share/{conversation.share_token}" if root_path else f"/IDEA/share/{conversation.share_token}"
```

## Testing Checklist

After implementing changes:

- [ ] Frontend files accessible: `http://localhost/IDEA/index.html`
- [ ] Login page accessible: `http://localhost/IDEA/login.html`
- [ ] API endpoints work: `http://localhost/IDEA/api/login`
- [ ] Idea-API endpoints work: `http://localhost/IDEA/idea-api/knowledge-base/papers`
- [ ] Static files accessible: `http://localhost/IDEA/static/...`
- [ ] Conversations work: `http://localhost/IDEA/conversations/...`
- [ ] Share pages work: `http://localhost/IDEA/share/{token}`
- [ ] CSS/JS assets load correctly (relative paths should work)
- [ ] WebSocket/EventSource streaming works for chat
- [ ] File uploads work
- [ ] Authentication flow works end-to-end

## Benefits

1. **Simplified Load Balancing**: All traffic routes through one backend service
2. **Easier Deployment**: Fewer containers to manage
3. **Consistent Path Structure**: Everything under `/IDEA/` prefix
4. **Better Scalability**: Can easily add multiple backend instances behind load balancer

## Rollback Plan

If issues arise:

1. Revert nginx.conf to previous version
2. Remove the `/IDEA` mount from `app.py`
3. Restore frontend service in `docker-compose.override.yml`
4. Revert `config.example.js` changes

## Notes

- The FastAPI app has `root_path="/idea-api"`, which means routes are internally prefixed with `/idea-api`
- Nginx handles the path translation from `/IDEA/` to `/idea-api/IDEA/` for the backend
- Frontend HTML files use relative paths for assets, which will work correctly when served from `/IDEA/`
- The `/share/{share_token}` route in `app.py` already serves `share.html` correctly
