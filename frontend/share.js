/**
 * Shared Conversation Viewer
 * Handles loading and displaying shared conversations in read-only mode
 */

const sharedStdoutMap = new Map();
const sharedMessageCache = new Map();
let lastSharedCodeId = null;
const SHARED_STD_STREAM_RECIPIENTS = ['stdout', 'stderr'];
const SHARED_IMAGE_LIGHTBOX_INITIAL_SCALE = 1.25;
const SHARED_IMAGE_LIGHTBOX_MIN_SCALE = 0.5;
const SHARED_IMAGE_LIGHTBOX_MAX_SCALE = 4;
const SHARED_IMAGE_LIGHTBOX_SCALE_STEP = 0.25;
const SHARED_COMPACTION_MARKER_PREFIX = '[IDEA conversation compacted at ';
const SHARED_COMPACTION_MARKER_SUFFIX = ']';
let sharedImageLightboxState = {
    scale: SHARED_IMAGE_LIGHTBOX_INITIAL_SCALE,
    fitScale: SHARED_IMAGE_LIGHTBOX_INITIAL_SCALE
};
let sharedCodeApplyAllEnabled = false;
let sharedCodeVisibilityAllMode = null;
let sharedOutputApplyAllEnabled = false;
let sharedOutputVisibilityAllMode = null;

//// Math formatting helpers for shared/downloaded views
function protectMath(text) {
    const store = [];
    const protect = (regex) => (src) =>
        src.replace(regex, (match) => {
            const key = `@@MATH${store.length}@@`;
            store.push(match);
            return key;
        });

    let out = protect(/\$\$([\s\S]*?)\$\$/g)(text);
    out = protect(/\\\[([\s\S]*?)\\\]/g)(out);
    out = protect(/(?<!\$)\$([^\n]+?)\$(?!\$)/g)(out);
    out = protect(/\\\(([^\n]+?)\\\)/g)(out);

    return { text: out, store };
}

function restoreMath(html, store) {
    return store.reduce((acc, original, index) => acc.replace(`@@MATH${index}@@`, original), html);
}

function countUnescapedSequence(text, sequence) {
    if (!text || !sequence) return 0;
    let count = 0;
    let index = text.indexOf(sequence);
    while (index !== -1) {
        let backslashCount = 0;
        let cursor = index - 1;
        while (cursor >= 0 && text[cursor] === '\\') {
            backslashCount += 1;
            cursor -= 1;
        }
        if (backslashCount % 2 === 0) {
            count += 1;
        }
        index = text.indexOf(sequence, index + sequence.length);
    }
    return count;
}

function hasBalancedMath(text) {
    const dollars = countUnescapedSequence(text, '$$') % 2 === 0;
    const lb = (text.match(/\\\[/g) || []).length;
    const rb = (text.match(/\\\]/g) || []).length;
    const lp = (text.match(/\\\(/g) || []).length;
    const rp = (text.match(/\\\)/g) || []).length;
    return dollars && lb === rb && lp === rp;
}
//// End math helpers

function addCopyButtonsShared(root) {
    const scope = root instanceof Element ? root : document;
    const codeBlocks = scope.querySelectorAll('pre code');
    codeBlocks.forEach(codeBlock => {
        const pre = codeBlock.parentElement;
        if (!pre) return;
        if (pre.querySelector('.copy-button')) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'copy-button';
        button.innerText = 'Copy';
        pre.appendChild(button);
        button.addEventListener('click', () => {
            navigator.clipboard.writeText(codeBlock.innerText).then(() => {
                button.innerText = 'Copied!';
                setTimeout(() => button.innerText = 'Copy', 2000);
            }).catch(() => {
                button.innerText = 'Error';
                setTimeout(() => button.innerText = 'Copy', 2000);
            });
        });
    });
}

function parseSharedCompactionMarker(content) {
    if (typeof content !== 'string') return null;
    const trimmed = content.trim();
    if (!trimmed.startsWith(SHARED_COMPACTION_MARKER_PREFIX) || !trimmed.endsWith(SHARED_COMPACTION_MARKER_SUFFIX)) {
        return null;
    }
    const timestamp = trimmed.slice(SHARED_COMPACTION_MARKER_PREFIX.length, -SHARED_COMPACTION_MARKER_SUFFIX.length);
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return null;
    return { timestamp, date };
}

function isSharedCompactionMarker(message) {
    return Boolean(message && message.message_type === 'message' && parseSharedCompactionMarker(message.content));
}

function formatSharedCompactionTime(date) {
    return date.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        timeZoneName: 'short'
    });
}

function renderSharedCompactionMarker(message) {
    const marker = parseSharedCompactionMarker(message?.content) || { date: new Date(message?.created_at || Date.now()) };
    return `
        <div class="compaction-marker" role="status">
            <span class="material-icons compaction-marker-icon" aria-hidden="true">compress</span>
            <span>Conversation compacted ${escapeHtml(formatSharedCompactionTime(marker.date))}</span>
        </div>
    `;
}

function getSharedImageLightboxElements() {
    return {
        modal: document.getElementById('sharedImageLightboxModal'),
        preview: document.getElementById('sharedImageLightboxPreview'),
        stage: document.getElementById('sharedImageLightboxStage'),
        viewport: document.getElementById('sharedImageLightboxViewport'),
        close: document.getElementById('closeSharedImageLightboxModal'),
        zoomIn: document.getElementById('sharedImageZoomInButton'),
        zoomOut: document.getElementById('sharedImageZoomOutButton'),
        zoomReset: document.getElementById('sharedImageZoomResetButton')
    };
}

function clampSharedImageZoom(scale) {
    return Math.min(
        SHARED_IMAGE_LIGHTBOX_MAX_SCALE,
        Math.max(sharedImageLightboxState.fitScale || SHARED_IMAGE_LIGHTBOX_MIN_SCALE, scale)
    );
}

function getSharedImageLightboxFitScale(preview, viewport, stage) {
    const naturalWidth = preview.naturalWidth || preview.width;
    const naturalHeight = preview.naturalHeight || preview.height;
    if (!naturalWidth || !naturalHeight) return SHARED_IMAGE_LIGHTBOX_INITIAL_SCALE;

    const stageStyles = window.getComputedStyle(stage);
    const paddingX = (parseFloat(stageStyles.paddingLeft) || 0) + (parseFloat(stageStyles.paddingRight) || 0);
    const paddingY = (parseFloat(stageStyles.paddingTop) || 0) + (parseFloat(stageStyles.paddingBottom) || 0);
    const availableWidth = Math.max(viewport.clientWidth - paddingX, 1);
    const availableHeight = Math.max(viewport.clientHeight - paddingY, 1);

    return Math.min(1, availableWidth / naturalWidth, availableHeight / naturalHeight);
}

function applySharedImageLightboxZoom() {
    const { preview, stage, viewport } = getSharedImageLightboxElements();
    if (!preview || !stage || !viewport) return;
    const naturalWidth = preview.naturalWidth || preview.width;
    const naturalHeight = preview.naturalHeight || preview.height;
    if (!naturalWidth || !naturalHeight) return;

    const scaledWidth = naturalWidth * sharedImageLightboxState.scale;
    const scaledHeight = naturalHeight * sharedImageLightboxState.scale;
    const stageWidth = Math.max(scaledWidth, viewport.clientWidth);
    const stageHeight = Math.max(scaledHeight, viewport.clientHeight);

    stage.style.width = `${stageWidth}px`;
    stage.style.height = `${stageHeight}px`;
    preview.style.width = `${scaledWidth}px`;
    preview.style.height = `${scaledHeight}px`;
}

function openSharedImageLightbox(src, alt = 'Expanded shared image') {
    const { modal, preview, viewport, stage } = getSharedImageLightboxElements();
    if (!modal || !preview || !viewport || !stage) return;
    preview.src = src;
    preview.alt = alt;
    preview.draggable = false;
    preview.onload = () => {
        sharedImageLightboxState.fitScale = getSharedImageLightboxFitScale(preview, viewport, stage);
        sharedImageLightboxState.scale = sharedImageLightboxState.fitScale;
        applySharedImageLightboxZoom();
        viewport.scrollTop = 0;
        viewport.scrollLeft = 0;
    };
    modal.style.display = 'block';
}

function closeSharedImageLightbox() {
    const { modal, preview, stage } = getSharedImageLightboxElements();
    if (!modal || !preview || !stage) return;
    modal.style.display = 'none';
    preview.removeAttribute('src');
    preview.style.width = '';
    preview.style.height = '';
    stage.style.width = '';
    stage.style.height = '';
    sharedImageLightboxState.scale = SHARED_IMAGE_LIGHTBOX_INITIAL_SCALE;
    sharedImageLightboxState.fitScale = SHARED_IMAGE_LIGHTBOX_INITIAL_SCALE;
}

function updateSharedImageLightboxZoom(delta) {
    sharedImageLightboxState.scale = clampSharedImageZoom(sharedImageLightboxState.scale + delta);
    applySharedImageLightboxZoom();
}

function resetSharedImageLightboxZoom() {
    sharedImageLightboxState.scale = sharedImageLightboxState.fitScale;
    applySharedImageLightboxZoom();
}

function initializeSharedImageLightbox() {
    const { modal, close, zoomIn, zoomOut, zoomReset, viewport } = getSharedImageLightboxElements();
    if (!modal || !close || !zoomIn || !zoomOut || !zoomReset || !viewport) {
        return;
    }

    close.addEventListener('click', closeSharedImageLightbox);
    zoomIn.addEventListener('click', () => updateSharedImageLightboxZoom(SHARED_IMAGE_LIGHTBOX_SCALE_STEP));
    zoomOut.addEventListener('click', () => updateSharedImageLightboxZoom(-SHARED_IMAGE_LIGHTBOX_SCALE_STEP));
    zoomReset.addEventListener('click', resetSharedImageLightboxZoom);

    modal.addEventListener('click', (event) => {
        if (event.target === modal) {
            closeSharedImageLightbox();
        }
    });

    viewport.addEventListener('wheel', (event) => {
        if (modal.style.display !== 'block') return;
        event.preventDefault();
        const delta = event.deltaY < 0 ? SHARED_IMAGE_LIGHTBOX_SCALE_STEP : -SHARED_IMAGE_LIGHTBOX_SCALE_STEP;
        updateSharedImageLightboxZoom(delta);
    }, { passive: false });

    viewport.addEventListener('dragstart', (event) => {
        if (event.target instanceof HTMLImageElement) {
            event.preventDefault();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (modal.style.display !== 'block') return;
        if (event.key === 'Escape') {
            closeSharedImageLightbox();
        } else if (event.key === '+' || event.key === '=') {
            updateSharedImageLightboxZoom(SHARED_IMAGE_LIGHTBOX_SCALE_STEP);
        } else if (event.key === '-') {
            updateSharedImageLightboxZoom(-SHARED_IMAGE_LIGHTBOX_SCALE_STEP);
        } else if (event.key === '0') {
            resetSharedImageLightboxZoom();
        }
    });

    window.addEventListener('resize', () => {
        if (modal.style.display === 'block') {
            applySharedImageLightboxZoom();
        }
    });

    document.addEventListener('click', (event) => {
        const image = event.target.closest('#chatDisplay .chat-image-preview');
        if (!image) return;
        openSharedImageLightbox(image.currentSrc || image.src, image.alt || 'Expanded shared image');
    });
}

// Extract share token from URL
function getShareTokenFromUrl() {
    const path = window.location.pathname;
    const matches = path.match(/\/share\/([a-zA-Z0-9_-]+)/);
    return matches ? matches[1] : null;
}

// Determine whether a shared message should be rendered
function shouldDisplaySharedMessage(message) {
    if (!message) return false;
    normalizeSharedMessage(message);
    
    if (message.message_type === 'console') {
        return !isSharedTelemetryConsole(message);
    }
    
    return true;
}

function resetSharedStdoutState() {
    sharedStdoutMap.clear();
    lastSharedCodeId = null;
    sharedMessageCache.clear();
}

function normalizeSharedMessage(message) {
    if (!message) return message;
    if (!message.message_type && message.type) {
        message.message_type = message.type;
    }
    if (!message.message_format && message.format) {
        message.message_format = message.format;
    }
    if (!message.recipient && message.message_recipient) {
        message.recipient = message.message_recipient;
    }

    const recipient = (message.recipient || '').toLowerCase();
    if ((message.message_type === 'message' || message.message_type === 'text') &&
        SHARED_STD_STREAM_RECIPIENTS.includes(recipient)) {
        message.message_type = 'console';
        message.message_format = message.message_format || recipient || 'output';
    } else if (message.message_type === 'console' && !message.message_format) {
        message.message_format = recipient || 'output';
    }
    return message;
}

function shouldTrackSharedCode(message) {
    return Boolean(
        message &&
        message.message_type === 'code' &&
        message.role !== 'user' &&
        message.message_format !== 'html'
    );
}

function isSharedConsoleMessage(message) {
    return Boolean(
        message &&
        message.message_type === 'console' &&
        message.message_format !== 'active_line' &&
        !isSharedTelemetryConsole(message)
    );
}

function isSharedTelemetryConsole(message) {
    if (!message || message.message_type !== 'console') return false;
    if (message.message_format === 'active_line') return true;
    const content = typeof message.content === 'string' ? message.content.trim() : '';
    if (message.message_format === 'execution' && /^\d+(?:\/\d+)?$/.test(content)) {
        return true;
    }
    if (/^line\s+\d+$/i.test(content)) {
        return true;
    }
    return false;
}

function findSharedPreviousCodeId(referenceId) {
    if (!referenceId) return null;
    const chatDisplay = document.getElementById('chatDisplay');
    const messages = Array.from(chatDisplay.querySelectorAll('.message'));
    const index = messages.findIndex(el => el.getAttribute('data-id') === referenceId);
    if (index === -1) return null;
    for (let i = index - 1; i >= 0; i--) {
        const candidateId = messages[i].getAttribute('data-id');
        const data = sharedMessageCache.get(candidateId);
        if (data && shouldTrackSharedCode(data)) {
            return candidateId;
        }
    }
    return null;
}

function ensureSharedStdoutElements(codeId) {
    const messageElement = document.querySelector(`.message[data-id="${codeId}"]`);
    if (!messageElement) return {};
    const contentElement = messageElement.querySelector('.content');
    if (!contentElement) return {};

    let controls = messageElement.querySelector('.stdout-controls');
    let button = controls ? controls.querySelector('.stdout-button') : null;
    let panel = messageElement.querySelector('.stdout-panel');

    if (!controls) {
        controls = document.createElement('div');
        controls.className = 'stdout-controls stdout-hidden';
        button = document.createElement('button');
        button.className = 'stdout-button';
        button.type = 'button';
        button.textContent = 'Show Output';
        button.setAttribute('aria-expanded', 'false');
        button.disabled = true;
        button.addEventListener('click', () => toggleSharedStdoutPanel(codeId));
        const applyAllLabel = document.createElement('label');
        applyAllLabel.className = 'output-apply-all';
        const applyAllCheckbox = document.createElement('input');
        applyAllCheckbox.type = 'checkbox';
        applyAllCheckbox.checked = sharedOutputApplyAllEnabled;
        applyAllCheckbox.addEventListener('change', () => {
            sharedOutputApplyAllEnabled = applyAllCheckbox.checked;
            if (!sharedOutputApplyAllEnabled) {
                sharedOutputVisibilityAllMode = null;
            } else {
                const currentPanel = messageElement.querySelector('.stdout-panel');
                sharedOutputVisibilityAllMode = Boolean(currentPanel?.classList.contains('open'));
            }
            syncSharedOutputApplyAllCheckboxes();
        });
        applyAllLabel.appendChild(applyAllCheckbox);
        applyAllLabel.appendChild(document.createTextNode(' all'));
        controls.appendChild(button);
        controls.appendChild(applyAllLabel);
        contentElement.appendChild(controls);
    }

    if (!panel) {
        panel = document.createElement('div');
        panel.className = 'stdout-panel';
        panel.setAttribute('role', 'region');
        panel.setAttribute('aria-label', 'STDOUT and STDERR');
        panel.setAttribute('aria-hidden', 'true');
        contentElement.appendChild(panel);
    }

    syncSharedOutputApplyAllCheckboxes();
    return { messageElement, contentElement, controls, button, panel };
}

function getSharedOutputMessageElements() {
    return Array.from(document.querySelectorAll('.message')).filter(element => {
        const codeId = element.getAttribute('data-id');
        return (sharedStdoutMap.get(codeId) || []).length > 0;
    });
}

function syncSharedOutputApplyAllCheckboxes() {
    document.querySelectorAll('.output-apply-all input[type="checkbox"]').forEach(checkbox => {
        checkbox.checked = sharedOutputApplyAllEnabled;
    });
}

function setSharedStdoutVisibility(codeId, showOutput, { autoScroll = false } = {}) {
    const { button, panel } = ensureSharedStdoutElements(codeId);
    if (!button || !panel || button.disabled) return;
    panel.classList.toggle('open', showOutput);
    panel.setAttribute('aria-hidden', showOutput ? 'false' : 'true');
    button.textContent = showOutput ? 'Hide Output' : 'Show Output';
    button.setAttribute('aria-expanded', String(showOutput));
    if (showOutput) {
        renderSharedStdoutPanel(codeId);
        if (autoScroll) {
            panel.scrollTop = panel.scrollHeight;
            panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    }
}

function applySharedStdoutVisibilityToAll(showOutput) {
    getSharedOutputMessageElements().forEach(element => {
        const codeId = element.getAttribute('data-id');
        setSharedStdoutVisibility(codeId, showOutput);
    });
}

function updateSharedStdoutAvailability(codeId) {
    const { controls, button, panel } = ensureSharedStdoutElements(codeId);
    if (!controls || !button) return;
    const hasOutput = (sharedStdoutMap.get(codeId) || []).length > 0;
    controls.classList.toggle('stdout-hidden', !hasOutput);
    if (!hasOutput) {
        button.disabled = false;
        setSharedStdoutVisibility(codeId, false);
        button.disabled = true;
    } else if (sharedOutputVisibilityAllMode !== null) {
        button.disabled = false;
        setSharedStdoutVisibility(codeId, sharedOutputVisibilityAllMode);
    } else {
        button.disabled = false;
    }
}

function addSharedConsoleOutput(codeId, message) {
    if (!codeId) return;
    const text = typeof message.content === 'string' ? message.content : '';
    if (!text.trim()) return;
    if (!sharedStdoutMap.has(codeId)) {
        sharedStdoutMap.set(codeId, []);
    }
    sharedStdoutMap.get(codeId).push(text);
    updateSharedStdoutAvailability(codeId);
    const { panel } = ensureSharedStdoutElements(codeId);
    if (panel && panel.classList.contains('open')) {
        renderSharedStdoutPanel(codeId);
        panel.scrollTop = panel.scrollHeight;
    }
}

function renderSharedStdoutPanel(codeId) {
    const { panel } = ensureSharedStdoutElements(codeId);
    if (!panel) return;
    panel.innerHTML = '';
    const outputs = sharedStdoutMap.get(codeId) || [];
    if (outputs.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'stdout-empty';
        empty.textContent = 'No console output captured.';
        panel.appendChild(empty);
        return;
    }
    outputs.forEach(content => {
        const entry = document.createElement('div');
        entry.className = 'stdout-entry';
        const pre = document.createElement('pre');
        pre.classList.add('stdout-pre');
        const code = document.createElement('code');
        code.classList.add('stdout-code');
        code.textContent = content;
        pre.appendChild(code);
        entry.appendChild(pre);
        panel.appendChild(entry);
    });
    if (outputs.length > 0) {
        addCopyButtonsShared(panel);
    }
    if (typeof Prism !== 'undefined') {
        Prism.highlightAllUnder(panel);
    }
}

function toggleSharedStdoutPanel(codeId) {
    const { button, panel } = ensureSharedStdoutElements(codeId);
    if (!button || !panel || button.disabled) return;
    const showOutput = !panel.classList.contains('open');
    if (sharedOutputApplyAllEnabled) {
        sharedOutputVisibilityAllMode = showOutput;
        applySharedStdoutVisibilityToAll(showOutput);
        if (showOutput) {
            panel.scrollTop = panel.scrollHeight;
            panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    } else {
        setSharedStdoutVisibility(codeId, showOutput, { autoScroll: showOutput });
    }
}

function getSharedCodeMessageElements() {
    return Array.from(document.querySelectorAll('.message')).filter(element => {
        const id = element.getAttribute('data-id');
        return shouldTrackSharedCode(sharedMessageCache.get(id));
    });
}

function setSharedCodeVisibility(codeId, showCode) {
    const messageElement = document.querySelector(`.message[data-id="${codeId}"]`);
    if (!messageElement) return;
    messageElement.classList.toggle('code-collapsed', !showCode);
    const button = messageElement.querySelector('.code-toggle-button');
    if (button) {
        button.textContent = showCode ? 'Hide Code' : 'Show Code';
        button.setAttribute('aria-expanded', String(showCode));
    }
}

function applySharedCodeVisibilityToAll(showCode) {
    getSharedCodeMessageElements().forEach(element => {
        setSharedCodeVisibility(element.getAttribute('data-id'), showCode);
    });
}

function syncSharedCodeApplyAllCheckboxes() {
    document.querySelectorAll('.code-apply-all input[type="checkbox"]').forEach(checkbox => {
        checkbox.checked = sharedCodeApplyAllEnabled;
    });
}

function ensureSharedCodeControls(codeId) {
    const messageElement = document.querySelector(`.message[data-id="${codeId}"]`);
    if (!messageElement) return;
    const contentElement = messageElement.querySelector('.content');
    const pre = contentElement?.querySelector(':scope > pre');
    const codeBlock = pre?.querySelector('code');
    if (!contentElement || !pre || !codeBlock || contentElement.querySelector(':scope > .code-controls')) return;

    const controls = document.createElement('div');
    controls.className = 'code-controls';

    const left = document.createElement('div');
    left.className = 'code-controls-left';

    const toggleButton = document.createElement('button');
    toggleButton.type = 'button';
    toggleButton.className = 'code-toggle-button';
    toggleButton.setAttribute('aria-expanded', 'true');
    toggleButton.textContent = 'Hide Code';
    toggleButton.addEventListener('click', () => {
        const showCode = messageElement.classList.contains('code-collapsed');
        if (sharedCodeApplyAllEnabled) {
            sharedCodeVisibilityAllMode = showCode;
            applySharedCodeVisibilityToAll(showCode);
        } else {
            setSharedCodeVisibility(codeId, showCode);
        }
    });

    const applyAllLabel = document.createElement('label');
    applyAllLabel.className = 'code-apply-all';
    const applyAllCheckbox = document.createElement('input');
    applyAllCheckbox.type = 'checkbox';
    applyAllCheckbox.checked = sharedCodeApplyAllEnabled;
    applyAllCheckbox.addEventListener('change', () => {
        sharedCodeApplyAllEnabled = applyAllCheckbox.checked;
        if (!sharedCodeApplyAllEnabled) {
            sharedCodeVisibilityAllMode = null;
        } else {
            sharedCodeVisibilityAllMode = !messageElement.classList.contains('code-collapsed');
        }
        syncSharedCodeApplyAllCheckboxes();
    });
    applyAllLabel.appendChild(applyAllCheckbox);
    applyAllLabel.appendChild(document.createTextNode(' all'));

    left.appendChild(toggleButton);
    left.appendChild(applyAllLabel);
    controls.appendChild(left);
    contentElement.insertBefore(controls, pre);
    addCopyButtonsShared(pre);

    if (sharedCodeVisibilityAllMode !== null) {
        setSharedCodeVisibility(codeId, sharedCodeVisibilityAllMode);
    }
    syncSharedCodeApplyAllCheckboxes();
}

// Display message in chat (similar to conversation_ui.js but simplified for read-only)
function displayMessageInChat(message) {
    if (!shouldDisplaySharedMessage(message)) {
        return;
    }
    normalizeSharedMessage(message);
    
    const chatDisplay = document.getElementById('chatDisplay');
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.role}`;
    const messageId = message.id || generateId('msg');
    messageDiv.setAttribute('data-id', messageId);
    if (!message.id) {
        message.id = messageId;
    }
    sharedMessageCache.set(messageId, message);
    
    const contentElement = document.createElement('div');
    contentElement.classList.add('content');
    contentElement.setAttribute('data-type', message.message_type);
    
    // Handle different message types and formats similar to conversation_ui.js
    if (isSharedCompactionMarker(message)) {
        messageDiv.classList.add('compaction-marker-message');
        contentElement.classList.add('compaction-marker-content');
        contentElement.innerHTML = renderSharedCompactionMarker(message);
    } else if (message.message_type === 'message') {
        const raw = message.content || '';
        const { text: shielded, store } = protectMath(raw);
        if (!hasBalancedMath(raw)) {
            contentElement.textContent = raw;
        } else {
            const parsedMarkdown = marked ? marked.parse(shielded) : shielded;
            contentElement.innerHTML = restoreMath(parsedMarkdown, store);
        }
    } else if (message.message_type === 'image') {
        if (message.message_format === 'base64.png') {
            contentElement.innerHTML = `<img src="data:image/png;base64,${message.content}" alt="Image" class="chat-image-preview">`;
        } else if (message.message_format === 'path') {
            contentElement.innerHTML = `<img src="${message.content}" alt="Image" class="chat-image-preview">`;
        } else {
            contentElement.innerHTML = `<img src="${message.content}" alt="Image" class="chat-image-preview">`;
        }
    } else if (message.message_type === 'code') {
        if (message.message_format === 'html') {
            contentElement.innerHTML = message.content;
        } else {
            const language = message.message_format || '';
            contentElement.innerHTML = `<pre><code class="language-${language}">${escapeHtml(message.content)}</code></pre>`;
        }
    } else if (message.message_type === 'console') {
        contentElement.innerHTML = '<pre><code></code></pre>';
        messageDiv.classList.add('console-output-message');
        contentElement.setAttribute('aria-hidden', 'true');
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
    if (!shouldTrackSharedCode(message)) {
        addCopyButtonsShared(contentElement);
    }
    
    if (shouldTrackSharedCode(message)) {
        lastSharedCodeId = messageId;
        ensureSharedCodeControls(messageId);
        ensureSharedStdoutElements(messageId);
    } else if (isSharedConsoleMessage(message)) {
        messageDiv.classList.add('console-output-message');
        contentElement.setAttribute('aria-hidden', 'true');
        let targetCodeId = lastSharedCodeId;
        if (!targetCodeId) {
            targetCodeId = findSharedPreviousCodeId(messageId);
        }
        if (targetCodeId) {
            ensureSharedStdoutElements(targetCodeId);
            addSharedConsoleOutput(targetCodeId, message);
            lastSharedCodeId = targetCodeId;
        }
    }
    
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

function parseUtcDate(dateString) {
    if (!dateString) return null;
    const value = String(dateString);
    const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
    const date = new Date(hasTimezone ? value : `${value}Z`);
    return Number.isNaN(date.getTime()) ? null : date;
}

// Format UTC timestamps for display in the viewer's browser timezone.
function formatDate(dateString) {
    const date = parseUtcDate(dateString);
    if (!date) return '';
    return date.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        timeZoneName: 'short'
    });
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
    if (!conversationInfo) return;
    
    const createdDate = formatDate(conversation.created_at);
    
    conversationInfo.innerHTML = `
        <span><strong>Created:</strong> ${createdDate}</span>
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
        resetSharedStdoutState();
        
        // Check if conversation has messages
        if (!conversation.messages || conversation.messages.length === 0) {
            showEmpty();
            return;
        }
        
        // Display messages
        conversation.messages
            .filter(shouldDisplaySharedMessage)
            .forEach(message => {
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
    initializeSharedImageLightbox();
    loadSharedConversation();
});
