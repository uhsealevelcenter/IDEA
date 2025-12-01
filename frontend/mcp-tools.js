(function () {
    const state = {
        connections: [],
        selectedConnectionId: null,
        tools: [],
        resources: [],
        prompts: [],
        selectedTool: null,
        selectedResource: null,
        selectedPrompt: null,
        activeTab: 'tools',
        lastResult: null,
        lastResultContext: null,
    };

    const elements = {};

    function getEndpoints() {
        try {
            return config.getEndpoints();
        } catch (err) {
            console.error('Unable to resolve endpoints:', err);
            return {};
        }
    }

    function getAuthHeaders() {
        const token = localStorage.getItem('authToken');
        return token ? { Authorization: `Bearer ${token}` } : {};
    }

    function cacheElements() {
        elements.modal = document.getElementById('mcpToolsModal');
        elements.openButtons = [
            document.getElementById('mcpToolsButton'),
            document.getElementById('mcpToolsButtonMobile'),
        ].filter(Boolean);
        elements.closeButton = document.getElementById('closeMcpToolsModal');
        elements.refreshButton = document.getElementById('refreshMcpConnectionsBtn');
        elements.connectionsList = document.getElementById('mcpToolsConnectionList');
        elements.selectedName = document.getElementById('mcpToolsSelectedName');
        elements.selectedDetails = document.getElementById('mcpToolsSelectedDetails');
        elements.tabButtons = Array.from(document.querySelectorAll('.mcp-tab'));
        elements.tabPanels = Array.from(document.querySelectorAll('.mcp-tab-panel'));
        elements.toolsList = document.getElementById('mcpToolsList');
        elements.resourcesList = document.getElementById('mcpResourcesList');
        elements.promptsList = document.getElementById('mcpPromptsList');
        elements.toolArguments = document.getElementById('mcpToolArguments');
        elements.runToolButton = document.getElementById('runMcpToolBtn');
        elements.resourceButton = document.getElementById('readMcpResourceBtn');
        elements.promptArguments = document.getElementById('mcpPromptArguments');
        elements.runPromptButton = document.getElementById('runMcpPromptBtn');
        elements.resultContainer = document.getElementById('mcpResultContainer');
        elements.resultMessage = document.getElementById('mcpResultMessage');
        elements.resultOutput = document.getElementById('mcpResultOutput');
        elements.sendResultButton = document.getElementById('sendMcpResultToChatBtn');
        elements.selectedToolHeading = document.getElementById('mcpSelectedToolHeading');
        elements.selectedToolDescription = document.getElementById('mcpSelectedToolDescription');
        elements.selectedResourceHeading = document.getElementById('mcpSelectedResourceHeading');
        elements.selectedResourceDescription = document.getElementById('mcpSelectedResourceDescription');
        elements.selectedPromptHeading = document.getElementById('mcpSelectedPromptHeading');
        elements.selectedPromptDescription = document.getElementById('mcpSelectedPromptDescription');
        elements.actionSections = Array.from(document.querySelectorAll('.mcp-action-section'));
    }

    function attachEvents() {
        elements.openButtons.forEach((btn) => btn.addEventListener('click', openModal));
        if (elements.closeButton) elements.closeButton.addEventListener('click', closeModal);
        if (elements.refreshButton) elements.refreshButton.addEventListener('click', () => loadConnections(true));
        if (elements.runToolButton) elements.runToolButton.addEventListener('click', invokeSelectedTool);
        if (elements.resourceButton) elements.resourceButton.addEventListener('click', fetchSelectedResource);
        if (elements.runPromptButton) elements.runPromptButton.addEventListener('click', fetchSelectedPrompt);
        if (elements.sendResultButton) elements.sendResultButton.addEventListener('click', sendResultToChat);

        elements.tabButtons.forEach((tab) => {
            tab.addEventListener('click', () => switchTab(tab.getAttribute('data-tab')));
        });

        window.addEventListener('click', (event) => {
            if (event.target === elements.modal) {
                closeModal();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && elements.modal && elements.modal.style.display === 'block') {
                closeModal();
            }
        });
    }

    function openModal() {
        if (!elements.modal) return;
        elements.modal.style.display = 'block';
        document.body.style.overflow = 'hidden';
        loadConnections();
    }

    function closeModal() {
        if (!elements.modal) return;
        elements.modal.style.display = 'none';
        document.body.style.overflow = '';
        resetState();
    }

    function resetState() {
        state.selectedConnectionId = null;
        state.tools = [];
        state.resources = [];
        state.prompts = [];
        state.selectedTool = null;
        state.selectedResource = null;
        state.selectedPrompt = null;
        state.lastResult = null;
        state.lastResultContext = null;
        elements.selectedName.textContent = 'Select a connection';
        elements.selectedDetails.textContent = '';
        renderConnectionsList();
        renderTools();
        renderResources();
        renderPrompts();
        updateToolActionPanel();
        updateResourceActionPanel();
        updatePromptActionPanel();
        setResult(null, '(No result yet)', 'info');
    }

    async function loadConnections(force) {
        if (!force && state.connections.length) return;
        try {
            const endpoints = getEndpoints();
            if (!endpoints.mcpConnectionsActive) throw new Error('MCP endpoints are not configured');
            const res = await fetch(endpoints.mcpConnectionsActive, {
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                throw new Error('Failed to load connections');
            }
            const data = await res.json();
            state.connections = Array.isArray(data) ? data : [];
            renderConnectionsList();
        } catch (err) {
            console.error('Unable to load MCP connections:', err);
            if (elements.connectionsList) {
                elements.connectionsList.innerHTML = '<div class="list-error">Unable to load connections.</div>';
            }
        }
    }

    function renderConnectionsList() {
        if (!elements.connectionsList) return;
        if (!state.connections.length) {
            elements.connectionsList.innerHTML = '<div class="empty-state">No active connections.</div>';
            return;
        }
        const html = state.connections
            .map((connection) => {
                const isActive = connection.id === state.selectedConnectionId;
                return `
                    <div class="prompt-item${isActive ? ' active' : ''}" data-connection-id="${connection.id}">
                        <div class="prompt-item-content">
                            <div class="prompt-item-name">${escapeHtml(connection.name)}</div>
                            <div class="prompt-item-meta">${escapeHtml(connection.transport)}</div>
                            <div class="prompt-item-tags">
                                <span class="status-badge ${connection.is_active ? 'badge-active' : 'badge-inactive'}">${connection.is_active ? 'Active' : 'Inactive'}</span>
                            </div>
                        </div>
                    </div>
                `;
            })
            .join('');
        elements.connectionsList.innerHTML = html;
        Array.from(elements.connectionsList.querySelectorAll('[data-connection-id]')).forEach((el) => {
            el.addEventListener('click', () => {
                const id = el.getAttribute('data-connection-id');
                selectConnection(id);
            });
        });
    }

    async function selectConnection(id) {
        const connection = state.connections.find((conn) => conn.id === id);
        if (!connection) return;
        state.selectedConnectionId = id;
        elements.selectedName.textContent = connection.name;
        const lastConnected = connection.last_connected_at
            ? `Last connected ${new Date(connection.last_connected_at).toLocaleString()}`
            : 'Never connected';
        elements.selectedDetails.textContent = `${connection.transport.toUpperCase()} • ${lastConnected}`;
        renderConnectionsList();
        switchTab(state.activeTab); // ensure correct panel visibility
        await Promise.all([loadTools(), loadResources(), loadPrompts()]);
        setResult(null, '(No result yet)', 'info');
    }

    function buildConnectionUrl(path) {
        const endpoints = getEndpoints();
        if (!endpoints.mcpConnections) throw new Error('MCP endpoints not configured');
        return `${endpoints.mcpConnections}/${state.selectedConnectionId}/${path}`;
    }

    async function loadTools() {
        if (!state.selectedConnectionId) return;
        try {
            const res = await fetch(buildConnectionUrl('tools'), {
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                throw await extractError(res, 'Unable to load tools');
            }
            const data = await res.json();
            const tools = Array.isArray(data?.tools) ? data.tools : Array.isArray(data) ? data : [];
            state.tools = tools;
            renderTools();
        } catch (err) {
            console.error('Failed to load tools:', err);
            state.tools = [];
            renderTools(err.message);
        }
    }

    async function loadResources() {
        if (!state.selectedConnectionId) return;
        try {
            const res = await fetch(buildConnectionUrl('resources'), {
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                throw await extractError(res, 'Unable to load resources');
            }
            const data = await res.json();
            const resources = Array.isArray(data?.resources) ? data.resources : Array.isArray(data) ? data : [];
            state.resources = resources;
            renderResources();
        } catch (err) {
            console.warn('Resources not available:', err);
            state.resources = [];
            renderResources(err.message);
        }
    }

    async function loadPrompts() {
        if (!state.selectedConnectionId) return;
        try {
            const res = await fetch(buildConnectionUrl('prompts'), {
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                throw await extractError(res, 'Unable to load prompts');
            }
            const data = await res.json();
            const prompts = Array.isArray(data?.prompts) ? data.prompts : Array.isArray(data) ? data : [];
            state.prompts = prompts;
            renderPrompts();
        } catch (err) {
            console.warn('Prompts not available:', err);
            state.prompts = [];
            renderPrompts(err.message);
        }
    }

    function renderTools(errorMessage) {
        if (!elements.toolsList) return;
        if (errorMessage) {
            elements.toolsList.innerHTML = `<div class="list-error">${escapeHtml(errorMessage)}</div>`;
            return;
        }
        if (!state.tools.length) {
            elements.toolsList.innerHTML = '<div class="empty-state">No tools available.</div>';
            return;
        }
        elements.toolsList.innerHTML = state.tools
            .map((tool) => {
                const isSelected = state.selectedTool && state.selectedTool.name === tool.name;
                return `
                    <div class="prompt-item${isSelected ? ' active' : ''}" data-tool-name="${tool.name}">
                        <div class="prompt-item-content">
                            <div class="prompt-item-name">${escapeHtml(tool.name)}</div>
                            <div class="prompt-item-meta">${escapeHtml(tool.description || 'No description')}</div>
                        </div>
                    </div>
                `;
            })
            .join('');
        Array.from(elements.toolsList.querySelectorAll('[data-tool-name]')).forEach((el) => {
            el.addEventListener('click', () => {
                const name = el.getAttribute('data-tool-name');
                const tool = state.tools.find((item) => item.name === name);
                selectTool(tool);
            });
        });
    }

    function renderResources(errorMessage) {
        if (!elements.resourcesList) return;
        if (errorMessage) {
            elements.resourcesList.innerHTML = `<div class="list-error">${escapeHtml(errorMessage)}</div>`;
            return;
        }
        if (!state.resources.length) {
            elements.resourcesList.innerHTML = '<div class="empty-state">No resources available.</div>';
            return;
        }
        elements.resourcesList.innerHTML = state.resources
            .map((resource) => {
                const isSelected = state.selectedResource && state.selectedResource.uri === resource.uri;
                return `
                    <div class="prompt-item${isSelected ? ' active' : ''}" data-resource-uri="${encodeURIComponent(resource.uri)}">
                        <div class="prompt-item-content">
                            <div class="prompt-item-name">${escapeHtml(resource.name || resource.uri)}</div>
                            <div class="prompt-item-meta">${escapeHtml(resource.uri)}</div>
                        </div>
                    </div>
                `;
            })
            .join('');
        Array.from(elements.resourcesList.querySelectorAll('[data-resource-uri]')).forEach((el) => {
            el.addEventListener('click', () => {
                const uri = decodeURIComponent(el.getAttribute('data-resource-uri'));
                const resource = state.resources.find((item) => item.uri === uri);
                selectResource(resource);
            });
        });
    }

    function renderPrompts(errorMessage) {
        if (!elements.promptsList) return;
        if (errorMessage) {
            elements.promptsList.innerHTML = `<div class="list-error">${escapeHtml(errorMessage)}</div>`;
            return;
        }
        if (!state.prompts.length) {
            elements.promptsList.innerHTML = '<div class="empty-state">No prompts available.</div>';
            return;
        }
        elements.promptsList.innerHTML = state.prompts
            .map((prompt) => {
                const isSelected = state.selectedPrompt && state.selectedPrompt.name === prompt.name;
                return `
                    <div class="prompt-item${isSelected ? ' active' : ''}" data-prompt-name="${prompt.name}">
                        <div class="prompt-item-content">
                            <div class="prompt-item-name">${escapeHtml(prompt.name)}</div>
                            <div class="prompt-item-meta">${escapeHtml(prompt.description || '')}</div>
                        </div>
                    </div>
                `;
            })
            .join('');
        Array.from(elements.promptsList.querySelectorAll('[data-prompt-name]')).forEach((el) => {
            el.addEventListener('click', () => {
                const name = el.getAttribute('data-prompt-name');
                const prompt = state.prompts.find((item) => item.name === name);
                selectPrompt(prompt);
            });
        });
    }

    function selectTool(tool) {
        state.selectedTool = tool || null;
        renderTools();
        updateToolActionPanel();
    }

    function selectResource(resource) {
        state.selectedResource = resource || null;
        renderResources();
        updateResourceActionPanel();
    }

    function selectPrompt(prompt) {
        state.selectedPrompt = prompt || null;
        renderPrompts();
        updatePromptActionPanel();
    }

    function updateToolActionPanel() {
        if (!state.selectedTool) {
            elements.selectedToolHeading.textContent = 'Tool Invocation';
            elements.selectedToolDescription.textContent = 'Select a tool to view details.';
            elements.toolArguments.value = '';
            elements.toolArguments.disabled = true;
            elements.runToolButton.disabled = true;
            return;
        }

        elements.selectedToolHeading.textContent = `Invoke ${state.selectedTool.name}`;
        elements.selectedToolDescription.textContent = state.selectedTool.description || 'No description provided.';
        elements.toolArguments.disabled = false;
        elements.runToolButton.disabled = false;

        // Always update arguments when a new tool is selected
        elements.toolArguments.value = buildArgumentTemplate(state.selectedTool) || '{}';
    }

    function updateResourceActionPanel() {
        const hasResource = Boolean(state.selectedResource);
        elements.resourceButton.disabled = !hasResource;
        elements.selectedResourceHeading.textContent = hasResource
            ? `Resource: ${state.selectedResource.name || state.selectedResource.uri}`
            : 'Resource Viewer';
        elements.selectedResourceDescription.textContent = hasResource
            ? state.selectedResource.description || state.selectedResource.uri
            : 'Select a resource to preview its contents.';
    }

    function updatePromptActionPanel() {
        const hasPrompt = Boolean(state.selectedPrompt);
        elements.runPromptButton.disabled = !hasPrompt;
        elements.promptArguments.disabled = !hasPrompt;
        elements.selectedPromptHeading.textContent = hasPrompt
            ? `Prompt: ${state.selectedPrompt.name}`
            : 'Prompt Viewer';
        elements.selectedPromptDescription.textContent = hasPrompt
            ? state.selectedPrompt.description || 'No description provided.'
            : 'Select a prompt to fetch its content.';
        if (hasPrompt && !elements.promptArguments.value.trim()) {
            elements.promptArguments.value = '{}';
        }
    }

    function buildArgumentTemplate(tool) {
        const schema = tool?.input_schema || tool?.inputSchema;
        if (!schema || typeof schema !== 'object') return '{}';
        try {
            const template = {};
            if (schema.properties && typeof schema.properties === 'object') {
                Object.entries(schema.properties).forEach(([key, value]) => {
                    template[key] = guessExampleValue(value);
                });
            }
            return JSON.stringify(template, null, 2);
        } catch (err) {
            console.warn('Unable to build argument template:', err);
            return '{}';
        }
    }

    function guessExampleValue(property) {
        if (!property || typeof property !== 'object') return null;
        if (property.examples && property.examples.length) {
            return property.examples[0];
        }
        const type = property.type;
        switch (type) {
            case 'string':
                return '';
            case 'number':
            case 'integer':
                return 0;
            case 'boolean':
                return false;
            case 'array':
                return [];
            case 'object':
                return {};
            default:
                return null;
        }
    }

    async function invokeSelectedTool() {
        if (!state.selectedConnectionId || !state.selectedTool) return;
        let args;
        try {
            args = elements.toolArguments.value.trim() ? JSON.parse(elements.toolArguments.value) : {};
        } catch (err) {
            setResult(null, `Invalid JSON arguments: ${err.message}`, 'error');
            return;
        }

        try {
            setResult(null, 'Running tool...', 'info');
            const res = await fetch(buildConnectionUrl(`tools/${encodeURIComponent(state.selectedTool.name)}`), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
                body: JSON.stringify({ arguments: args }),
            });
            if (!res.ok) {
                throw await extractError(res, 'Tool execution failed');
            }
            const data = await res.json();
            state.lastResult = data;
            state.lastResultContext = {
                type: 'tool',
                tool: state.selectedTool,
                connection: getSelectedConnection(),
            };
            setResult(data, 'Tool executed successfully.', 'success');
        } catch (err) {
            console.error('Tool invocation failed:', err);
            state.lastResult = null;
            setResult(null, err.message || 'Tool execution failed.', 'error');
        }
    }

    async function fetchSelectedResource() {
        if (!state.selectedConnectionId || !state.selectedResource) return;
        try {
            setResult(null, 'Fetching resource...', 'info');
            const url = buildConnectionUrl(`resources/${encodeURIComponent(state.selectedResource.uri)}`);
            const res = await fetch(url, {
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                throw await extractError(res, 'Failed to read resource');
            }
            const data = await res.json();
            state.lastResult = data;
            state.lastResultContext = {
                type: 'resource',
                resource: state.selectedResource,
                connection: getSelectedConnection(),
            };
            setResult(data, 'Resource loaded.', 'success');
        } catch (err) {
            console.error('Resource fetch failed:', err);
            setResult(null, err.message || 'Failed to read resource.', 'error');
        }
    }

    async function fetchSelectedPrompt() {
        if (!state.selectedConnectionId || !state.selectedPrompt) return;
        let args;
        try {
            args = elements.promptArguments.value.trim() ? JSON.parse(elements.promptArguments.value) : {};
        } catch (err) {
            setResult(null, `Invalid JSON arguments: ${err.message}`, 'error');
            return;
        }
        try {
            setResult(null, 'Fetching prompt...', 'info');
            const res = await fetch(buildConnectionUrl(`prompts/${encodeURIComponent(state.selectedPrompt.name)}`), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
                body: JSON.stringify({ arguments: args }),
            });
            if (!res.ok) {
                throw await extractError(res, 'Failed to load prompt');
            }
            const data = await res.json();
            state.lastResult = data;
            state.lastResultContext = {
                type: 'prompt',
                prompt: state.selectedPrompt,
                connection: getSelectedConnection(),
            };
            setResult(data, 'Prompt loaded.', 'success');
        } catch (err) {
            console.error('Prompt fetch failed:', err);
            setResult(null, err.message || 'Failed to load prompt.', 'error');
        }
    }

    function setResult(data, message, status) {
        if (elements.resultMessage) {
            elements.resultMessage.textContent = message || '';
            elements.resultMessage.className = `form-message${status ? ` ${status}` : ''}`;
        }
        if (elements.sendResultButton) {
            elements.sendResultButton.disabled = !data;
        }
        if (!elements.resultOutput) return;
        if (!data) {
            elements.resultOutput.textContent = '(No result)';
            return;
        }
        try {
            const formatted = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
            elements.resultOutput.textContent = formatted;
        } catch (err) {
            elements.resultOutput.textContent = '(Unable to display result)';
        }
    }

    function getSelectedConnection() {
        return state.connections.find((conn) => conn.id === state.selectedConnectionId) || null;
    }

    function switchTab(tabName) {
        state.activeTab = tabName;
        elements.tabButtons.forEach((tab) => {
            tab.classList.toggle('active', tab.getAttribute('data-tab') === tabName);
        });
        elements.tabPanels.forEach((panel) => {
            panel.classList.toggle('active', panel.getAttribute('data-tab') === tabName);
        });
        elements.actionSections.forEach((section) => {
            const target = section.getAttribute('data-panel');
            section.classList.toggle('hidden', target !== tabName);
        });
    }

    async function extractError(response, fallback) {
        try {
            const data = await response.json();
            if (data && data.detail) {
                return new Error(data.detail);
            }
        } catch (err) {
            /* ignore */
        }
        return new Error(fallback);
    }

    function sendResultToChat() {
        if (!state.lastResult || !state.lastResultContext || typeof window.appendExternalMessage !== 'function') {
            setResult(state.lastResult, 'Unable to send result to chat.', 'error');
            return;
        }
        const connection = state.lastResultContext.connection;
        let title = 'MCP Result';
        if (state.lastResultContext.type === 'tool' && state.lastResultContext.tool) {
            title = `MCP Tool • ${state.lastResultContext.tool.name}`;
        } else if (state.lastResultContext.type === 'resource' && state.lastResultContext.resource) {
            title = `MCP Resource • ${state.lastResultContext.resource.name || state.lastResultContext.resource.uri}`;
        } else if (state.lastResultContext.type === 'prompt' && state.lastResultContext.prompt) {
            title = `MCP Prompt • ${state.lastResultContext.prompt.name}`;
        }
        const header = connection ? `${title} (via ${connection.name})` : title;
        const body = formatResultForChat(state.lastResult);
        window.appendExternalMessage({
            role: 'computer',
            content: `**${header}**\n\n${body}`,
            type: 'message',
        });
        setResult(state.lastResult, 'Result sent to chat.', 'success');
    }

    function formatResultForChat(result) {
        if (typeof result === 'string') {
            return result;
        }
        try {
            return '```json\n' + JSON.stringify(result, null, 2) + '\n```';
        } catch (err) {
            return '```' + String(result) + '```';
        }
    }

    function escapeHtml(value) {
        return (value || '')
            .toString()
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    document.addEventListener('DOMContentLoaded', () => {
        cacheElements();
        if (!elements.modal) return;
        attachEvents();
        switchTab('tools');
        setResult(null, '(No result yet)', 'info');
    });
})();
