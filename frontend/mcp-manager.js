(function () {
    const state = {
        connections: [],
        selectedId: null,
        isLoading: false,
        isTesting: false,
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
        elements.modal = document.getElementById('mcpManagementModal');
        elements.openButtons = [
            document.getElementById('mcpManagementButton'),
            document.getElementById('mcpManagementButtonMobile'),
        ].filter(Boolean);
        elements.closeButton = document.getElementById('closeMcpManagementModal');
        elements.list = document.getElementById('mcpConnectionList');
        elements.createButton = document.getElementById('createMcpConnectionButton');
        elements.form = document.getElementById('mcpConnectionForm');
        elements.formTitle = document.getElementById('mcpFormTitle');
        elements.nameInput = document.getElementById('mcpName');
        elements.descriptionInput = document.getElementById('mcpDescription');
        elements.transportInput = document.getElementById('mcpTransport');
        elements.endpointInput = document.getElementById('mcpEndpoint');
        elements.commandInput = document.getElementById('mcpCommand');
        elements.commandArgsInput = document.getElementById('mcpCommandArgs');
        elements.headersInput = document.getElementById('mcpHeaders');
        elements.configInput = document.getElementById('mcpConfig');
        elements.authTokenInput = document.getElementById('mcpAuthToken');
        elements.clearTokenInput = document.getElementById('mcpClearToken');
        elements.activeInput = document.getElementById('mcpIsActive');
        elements.cancelButton = document.getElementById('cancelMcpEditBtn');
        elements.deleteButton = document.getElementById('deleteMcpConnectionBtn');
        elements.testButton = document.getElementById('testMcpConnectionBtn');
        elements.message = document.getElementById('mcpFormMessage');
        elements.meta = document.getElementById('mcpConnectionMeta');
        elements.transportDependent = Array.from(document.querySelectorAll('.transport-dependent'));
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
        resetForm();
    }

    function attachEvents() {
        elements.openButtons.forEach((btn) => btn.addEventListener('click', openModal));
        if (elements.closeButton) elements.closeButton.addEventListener('click', closeModal);
        if (elements.cancelButton) elements.cancelButton.addEventListener('click', resetForm);
        if (elements.createButton) elements.createButton.addEventListener('click', () => {
            resetForm();
            elements.nameInput.focus();
        });
        if (elements.deleteButton) elements.deleteButton.addEventListener('click', handleDelete);
        if (elements.testButton) elements.testButton.addEventListener('click', handleTestConnection);
        if (elements.form) elements.form.addEventListener('submit', handleFormSubmit);
        if (elements.transportInput) elements.transportInput.addEventListener('change', () => {
            updateTransportVisibility(elements.transportInput.value);
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

    async function loadConnections() {
        if (state.isLoading) return;
        state.isLoading = true;
        setMessage('', '');
        if (elements.list) {
            elements.list.innerHTML = '<div class="loading">Loading connections...</div>';
        }

        try {
            const endpoints = getEndpoints();
            if (!endpoints.mcpConnections) throw new Error('MCP endpoints are not configured');
            const res = await fetch(endpoints.mcpConnections, {
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                throw new Error('Unable to load connections');
            }
            const data = await res.json();
            state.connections = Array.isArray(data?.data) ? data.data : [];
            renderConnectionList();
        } catch (err) {
            console.error('Failed to load MCP connections:', err);
            if (elements.list) {
                elements.list.innerHTML = '<div class="list-error">Failed to load connections.</div>';
            }
        } finally {
            state.isLoading = false;
        }
    }

    function renderConnectionList() {
        if (!elements.list) return;
        if (!state.connections.length) {
            elements.list.innerHTML = '<div class="empty-state">No connections configured.</div>';
            return;
        }

        const items = state.connections
            .map((connection) => {
                const isActive = connection.id === state.selectedId;
                const badges = [
                    `<span class="status-badge">${connection.transport}</span>`,
                    `<span class="status-badge ${connection.is_active ? 'badge-active' : 'badge-inactive'}">${connection.is_active ? 'Active' : 'Inactive'}</span>`,
                ].join('');
                const lastConnected = connection.last_connected_at
                    ? new Date(connection.last_connected_at).toLocaleString()
                    : 'Never';
                return `
                    <div class="prompt-item${isActive ? ' active' : ''}" data-connection-id="${connection.id}">
                        <div class="prompt-item-content">
                            <div class="prompt-item-name">${escapeHtml(connection.name)}</div>
                            <div class="prompt-item-meta">Last connected: ${escapeHtml(lastConnected)}</div>
                            <div class="prompt-item-tags">${badges}</div>
                        </div>
                    </div>
                `;
            })
            .join('');
        elements.list.innerHTML = items;
        Array.from(elements.list.querySelectorAll('[data-connection-id]')).forEach((el) => {
            el.addEventListener('click', () => {
                const id = el.getAttribute('data-connection-id');
                selectConnection(id);
            });
        });
    }

    function selectConnection(id) {
        const connection = state.connections.find((item) => item.id === id);
        if (!connection) return;
        state.selectedId = id;
        renderConnectionList();
        populateForm(connection);
    }

    function populateForm(connection) {
        elements.formTitle.textContent = 'Edit Connection';
        elements.nameInput.value = connection.name || '';
        elements.descriptionInput.value = connection.description || '';
        elements.transportInput.value = connection.transport || 'streamable_http';
        elements.endpointInput.value = connection.endpoint || '';
        elements.commandInput.value = connection.command || '';
        elements.commandArgsInput.value = formatJson(connection.command_args, 'array');
        elements.headersInput.value = formatJson(connection.headers, 'object');
        elements.configInput.value = formatJson(connection.config, 'object');
        elements.authTokenInput.value = '';
        elements.clearTokenInput.checked = false;
        elements.activeInput.checked = Boolean(connection.is_active);
        if (elements.deleteButton) elements.deleteButton.disabled = false;
        updateTransportVisibility(elements.transportInput.value);
        setMetaInfo(connection);
        setMessage('', '');
    }

    function resetForm() {
        state.selectedId = null;
        if (elements.formTitle) elements.formTitle.textContent = 'Create Connection';
        elements.form.reset();
        elements.authTokenInput.value = '';
        elements.clearTokenInput.checked = false;
        elements.commandArgsInput.value = '';
        elements.headersInput.value = '';
        elements.configInput.value = '';
        updateTransportVisibility(elements.transportInput.value);
        if (elements.deleteButton) elements.deleteButton.disabled = true;
        setMetaInfo(null);
        setMessage('', '');
        renderConnectionList();
    }

    function updateTransportVisibility(transport) {
        elements.transportDependent.forEach((el) => {
            const visibleFor = (el.getAttribute('data-visible-for') || '').split(' ');
            if (visibleFor.includes(transport)) {
                el.classList.add('active');
            } else {
                el.classList.remove('active');
            }
        });
    }

    function formatJson(value, expectedType) {
        if (value === null || value === undefined) return '';
        try {
            if (expectedType === 'array' && Array.isArray(value)) {
                return JSON.stringify(value, null, 2);
            }
            if (expectedType === 'object' && typeof value === 'object') {
                return JSON.stringify(value, null, 2);
            }
        } catch (err) {
            console.warn('Failed to format JSON value:', err);
        }
        return '';
    }

    function parseJsonInput(source, expectedType) {
        const raw = source.value.trim();
        if (!raw) return expectedType === 'array' ? [] : {};
        try {
            const parsed = JSON.parse(raw);
            if (expectedType === 'array' && !Array.isArray(parsed)) {
                throw new Error('Expected an array');
            }
            if (expectedType === 'object' && typeof parsed !== 'object') {
                throw new Error('Expected an object');
            }
            return parsed;
        } catch (err) {
            throw new Error(`Invalid JSON in ${source.getAttribute('id') || 'field'}: ${err.message}`);
        }
    }

    function buildConnectionUrl(id) {
        const endpoints = getEndpoints();
        const base = endpoints.mcpConnections;
        if (!base) throw new Error('MCP endpoints are not configured');
        return `${base}/${id}`;
    }

    async function handleFormSubmit(event) {
        event.preventDefault();
        setMessage('', '');
        const isCreate = !state.selectedId;

        try {
            const payload = {
                name: elements.nameInput.value.trim(),
                description: elements.descriptionInput.value.trim() || null,
                transport: elements.transportInput.value,
                endpoint: elements.endpointInput.value.trim() || null,
                command: elements.commandInput.value.trim() || null,
                command_args: parseJsonInput(elements.commandArgsInput, 'array'),
                headers: parseJsonInput(elements.headersInput, 'object'),
                config: parseJsonInput(elements.configInput, 'object'),
                is_active: elements.activeInput.checked,
            };

            const tokenValue = elements.authTokenInput.value.trim();
            if (elements.clearTokenInput.checked) {
                payload.auth_token = '';
            } else if (tokenValue) {
                payload.auth_token = tokenValue;
            }

            if (!payload.name) {
                setMessage('Connection name is required.', 'error');
                return;
            }

            const endpoints = getEndpoints();
            if (!endpoints.mcpConnections) throw new Error('MCP endpoints are not available');

            const url = isCreate ? endpoints.mcpConnections : buildConnectionUrl(state.selectedId);
            const method = isCreate ? 'POST' : 'PUT';

            elements.form.querySelectorAll('button').forEach((btn) => (btn.disabled = true));

            const res = await fetch(url, {
                method,
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                const errPayload = await safeJson(res);
                throw new Error(errPayload?.detail || 'Failed to save connection');
            }

            const updated = await res.json();
            if (isCreate) {
                state.connections.unshift(updated);
                selectConnection(updated.id);
                setMessage('Connection created successfully.', 'success');
            } else {
                state.connections = state.connections.map((conn) => (conn.id === updated.id ? updated : conn));
                selectConnection(updated.id);
                setMessage('Connection updated successfully.', 'success');
            }
        } catch (err) {
            console.error('Failed to save connection:', err);
            setMessage(err.message || 'Failed to save connection.', 'error');
        } finally {
            elements.form.querySelectorAll('button').forEach((btn) => (btn.disabled = false));
        }
    }

    async function handleDelete() {
        if (!state.selectedId) return;
        const confirmation = confirm('Delete this connection? This action cannot be undone.');
        if (!confirmation) return;

        try {
            const res = await fetch(buildConnectionUrl(state.selectedId), {
                method: 'DELETE',
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                const errPayload = await safeJson(res);
                throw new Error(errPayload?.detail || 'Failed to delete connection');
            }
            state.connections = state.connections.filter((conn) => conn.id !== state.selectedId);
            resetForm();
            renderConnectionList();
            setMessage('Connection deleted.', 'success');
        } catch (err) {
            console.error('Failed to delete connection:', err);
            setMessage(err.message || 'Failed to delete connection.', 'error');
        }
    }

    async function handleTestConnection() {
        if (!state.selectedId) {
            setMessage('Save the connection before testing.', 'error');
            return;
        }
        if (state.isTesting) return;
        state.isTesting = true;
        setMessage('Testing connection...', 'info');

        try {
            const url = `${buildConnectionUrl(state.selectedId)}/tools`;
            const res = await fetch(url, {
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) {
                const errPayload = await safeJson(res);
                throw new Error(errPayload?.detail || 'Failed to query connection');
            }
            setMessage('Connection is reachable.', 'success');
            await refreshConnectionFromServer(state.selectedId);
        } catch (err) {
            console.error('Test connection failed:', err);
            setMessage(err.message || 'Connection test failed.', 'error');
        } finally {
            state.isTesting = false;
        }
    }

    async function refreshConnectionFromServer(id) {
        try {
            const res = await fetch(buildConnectionUrl(id), {
                headers: {
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) return;
            const updated = await res.json();
            state.connections = state.connections.map((conn) => (conn.id === updated.id ? updated : conn));
            selectConnection(updated.id);
        } catch (err) {
            console.warn('Unable to refresh connection details:', err);
        }
    }

    function setMessage(text, type) {
        if (!elements.message) return;
        elements.message.textContent = text;
        elements.message.className = `form-message${type ? ` ${type}` : ''}`;
    }

    function setMetaInfo(connection) {
        if (!elements.meta) return;
        if (!connection) {
            elements.meta.textContent = '';
            elements.meta.style.display = 'none';
            return;
        }
        const created = connection.created_at ? new Date(connection.created_at).toLocaleString() : 'Unknown';
        const updated = connection.updated_at ? new Date(connection.updated_at).toLocaleString() : 'Unknown';
        const last = connection.last_connected_at ? new Date(connection.last_connected_at).toLocaleString() : 'Never';
        elements.meta.innerHTML = `
            <strong>Created:</strong> ${escapeHtml(created)}<br>
            <strong>Updated:</strong> ${escapeHtml(updated)}<br>
            <strong>Last Connected:</strong> ${escapeHtml(last)}
        `;
        elements.meta.style.display = 'block';
    }

    function safeJson(response) {
        return response
            .json()
            .catch(() => ({}));
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
        updateTransportVisibility(elements.transportInput.value);
    });
})();
