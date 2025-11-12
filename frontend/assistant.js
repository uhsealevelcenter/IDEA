// script.js

// PREVENT DEFAULT BROWSER BEHAVIOR (drag and drop)
document.addEventListener('drop', (e) => e.preventDefault());

// Warm up the mic stream
let micStream = null;
async function warmUpMicrophone() {
    try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
        console.warn("Microphone warm-up failed or was denied:", err);
    }
}

// Constants and State Management
const INITIAL_TEXTAREA_HEIGHT = '38px';
// MESSAGE_TYPES is imported from conversation_manager.js

// Global State
let messages = [];
let currentMessageIndex = 0;
let isGenerating = false;
let controller = null;
let promptIdeasVisible = false;
const activeMessageIds = new Map();
let workingIndicatorId = null;
let pendingUploads = [];
let lastExecutableCodeId = null;
let pendingConsoleParentId = null;
const codeConsoleMap = new Map();
let activeLineCodeId = null;
let isActiveLineRunning = false;

const THEME_STORAGE_KEY = 'idea-theme';
const themeToggleInputs = document.querySelectorAll('[data-theme-toggle]');

// Conversation manager instance
let conversationManager;

// Authentication state
let authToken = localStorage.getItem('authToken');

// Authentication functions
async function checkAuthentication() {
    if (!authToken) {
        redirectToLogin();
        return false;
    }

    try {
        const response = await fetch(config.getEndpoints().verify, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            localStorage.removeItem('authToken');
            redirectToLogin();
            return false;
        }

        return true;
    } catch (error) {
        console.error('Auth verification error:', error);
        localStorage.removeItem('authToken');
        redirectToLogin();
        return false;
    }
}

function redirectToLogin() {
    window.location.href = 'login.html';
}

function getAuthHeaders() {
    return authToken ? { 'Authorization': `Bearer ${authToken}` } : {};
}

function logout() {
    localStorage.removeItem('authToken');
   authToken = null;
   redirectToLogin();
}

function applyTheme() {
    document.body.classList.remove('theme-light', 'theme-dark');
    document.body.classList.add('theme-light');

    themeToggleInputs.forEach((input) => {
        input.checked = false;
    });
}

function initializeTheme() {
    try {
        localStorage.setItem(THEME_STORAGE_KEY, 'light');
        applyTheme();
    } catch (error) {
        console.error('Failed to initialize theme:', error);
    }
}

initializeTheme();

//// Math formatting helpers

// Protect $$...$$, $...$, \[...\], \(...\) from Marked
function protectMath(text) {
  const store = [];

  // Order matters: block math first, then inline
  const protect = (regex) => (src) =>
    src.replace(regex, (m) => {
      const key = `@@MATH${store.length}@@`;
      store.push(m);             // keep the original, untouched
      return key;                // placeholder Marked won't touch
    });

  // Block: $$...$$ (multiline)
  let out = protect(/\$\$([\s\S]*?)\$\$/g)(text);

  // Display \[...\] (multiline)
  out = protect(/\\\[([\s\S]*?)\\\]/g)(out);

  // Inline: $...$ (not $$)
  out = protect(/(?<!\$)\$([^\n]+?)\$(?!\$)/g)(out);

  // Inline \( ... \)
  out = protect(/\\\(([^\n]+?)\\\)/g)(out);

  return { text: out, store };
}

function restoreMath(html, store) {
  return store.reduce((acc, m, i) => acc.replace(`@@MATH${i}@@`, m), html);
}

// Returns true if display/inline math is closed (so it's safe to typeset)
function hasBalancedMath(s) {
  // even number of $$ delimiters
  const dollars = (s.match(/\$\$/g) || []).length % 2 === 0;
  // balanced \[ \]
  const lb = (s.match(/\\\[/g) || []).length;
  const rb = (s.match(/\\\]/g) || []).length;
  // balanced \( \)
  const lp = (s.match(/\\\(/g) || []).length;
  const rp = (s.match(/\\\)/g) || []).length;
  return dollars && lb === rb && lp === rp;
}

// MathJax-safe typeset helper
function typeset(el) {
  if (!el) return Promise.resolve();
  if (!window.MathJax) return Promise.resolve();
  // Wait for MathJax startup (only once), then typeset the element
  if (MathJax.startup && MathJax.startup.promise) {
    return MathJax.startup.promise.then(() => MathJax.typesetPromise([el]));
  }
  // Fallback if startup.promise isn’t exposed for some reason
  if (MathJax.typesetPromise) {
    return MathJax.typesetPromise([el]);
  }
  return Promise.resolve();
}

function hasMathDelimiters(s) {
  return /\$\$|\\\[|\\\]|(?<!\$)\$[^\n]+?\$(?!\$)|\\\([^\n]+?\\\)/.test(s);
}

// queued typeset helper
let __mathQueue = Promise.resolve();

// Prism.js code highlighting helper
function prismHighlightUnder(el) {
  if (!el || !window.Prism) return;
  // Highlight only inside this container to avoid re-highlighting the whole page
  Prism.highlightAllUnder(el);
}
//// End of Math formatting helpers

// Session Management
let sessionId = generateId('session');
let threadId = localStorage.getItem('threadId') || (() => {
    const newThreadId = generateId('thread');
    localStorage.setItem('threadId', newThreadId);
    return newThreadId;
})();

// DOM Elements
const chatDisplay = document.getElementById('chatDisplay');
const sendButton = document.getElementById('sendButton');
const stopButton = document.getElementById('stopButton');
const newMessagesButton = document.getElementById('newMessagesButton');
const messageInput = document.getElementById('messageInput');
const progressBar = document.getElementById('uploadProgress');
const progressElement = progressBar ? progressBar.querySelector('.progress') : null;

async function handleFiles(files) {
    if (!files || files.length === 0) return;

    hidePromptIdeas(); 
    if (progressBar) {
        progressBar.style.display = 'block';
    }
    for (const file of files) {
        try {
            const response = await uploadFile(file, progressElement);
            queuePendingUpload(file, response);
        } catch (error) {
            appendSystemMessage(`Error uploading ${file.name}: ${error.message}`);
        }
    }
    if (progressBar) {
        progressBar.style.display = 'none';
    }
}

function queuePendingUpload(file, uploadResponse = {}) {
    const storedName = uploadResponse.filename || uploadResponse.name || file.name;
    const storagePath = uploadResponse.path || storedName;
    const isImage = (file.type || '').startsWith('image/');

    const attachment = {
        id: generateId('upload'),
        name: file.name,
        storedName,
        path: storagePath,
        size: file.size,
        mimeType: file.type,
        messageType: isImage ? 'image' : 'file',
        messageFormat: isImage ? 'path' : null
    };

    pendingUploads.push(attachment);
    renderPendingUploads();
}

function renderPendingUploads() {
    const uploadedFiles = document.getElementById('uploadedFiles');
    if (!uploadedFiles) return;

    uploadedFiles.innerHTML = '';

    if (pendingUploads.length === 0) {
        uploadedFiles.classList.remove('active');
        return;
    }

    uploadedFiles.classList.add('active');

    pendingUploads.forEach((attachment) => {
        const fileElement = document.createElement('span');
        fileElement.className = 'attached-file';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'attached-file-name';
        nameSpan.textContent = attachment.name;
        fileElement.appendChild(nameSpan);

        const removeButton = document.createElement('button');
        removeButton.className = 'remove-attachment';
        removeButton.setAttribute('aria-label', `Remove ${attachment.name}`);
        removeButton.textContent = '×';
        removeButton.addEventListener('click', () => removePendingAttachment(attachment.id));
        fileElement.appendChild(removeButton);

        uploadedFiles.appendChild(fileElement);
    });
}

async function removePendingAttachment(attachmentId) {
    const attachment = pendingUploads.find(att => att.id === attachmentId);
    if (!attachment) return;

    try {
        const response = await fetch(`${config.getEndpoints().files}/${encodeURIComponent(attachment.storedName)}`, {
            method: 'DELETE',
            headers: {
                'X-Session-Id': sessionId,
                ...getAuthHeaders()
            }
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Delete failed');
        }

        pendingUploads = pendingUploads.filter(att => att.id !== attachmentId);
        renderPendingUploads();
    } catch (error) {
        appendSystemMessage(`Error deleting file: ${error.message}`);
    }
}

function createPromptIdeas() {
    const container = document.getElementById('promptIdeasContainer');
    container.innerHTML = '';
    const promptsContainer = document.createElement('div');
    promptsContainer.className = 'prompt-ideas';
    promptsContainer.id = 'promptIdeas';
    console.log("Creating prompt ideas");
    const prompts = [
        {
            title: "Explore data", // Explore Popular Datasets
            prompt: "Explore a popular dataset for me, such as global population, climate data, or economic indicators. Load the data, clean it, and provide summaries or visualizations like interactive maps, time-series plots, or bar charts to help me understand the data better."
        },
        {
            title: "Analyze data", // Perform Data Analysis
            prompt: "Analyze a dataset for me. Calculate trends, perform statistical analysis, or apply machine learning models. Show me the code, results, and visualizations step-by-step."
        },
        {
            title: "Create maps", // Create Interactive Maps
            prompt: "Create an interactive map for me using geospatial data. For example, map population density, weather patterns, or transportation networks. Fetch the data, process it, and generate a map I can interact with."
        },
        {
            title: "Process files", // Generate Insights from Files
            prompt: "Process and analyze a file I upload, such as a CSV, Excel, or JSON file. Clean the data, extract insights, and create visualizations or reports for me."
        },
        {
            title: "Brainstorm ideas", // Brainstorm Research Ideas
            prompt: "Help me brainstorm research ideas using publicly available datasets. Suggest interesting questions, guide me through the initial analysis, and create visualizations to support the findings. If I don’t have a specific topic in mind, suggest one for me."
        },
        {
            title: "Fetch data", // Interact with APIs
            prompt: "Fetch data from an API or scrape data from a website (ethically and within legal boundaries). For example, retrieve weather data, stock prices, or other real-time information and analyze it for me."
        }
    ];

    const promptTitle = document.createElement('p');
    // promptTitle.className = 'prompt-title';
    // promptTitle.textContent = 'Prompt Ideas:';
    promptsContainer.appendChild(promptTitle);

    prompts.forEach(prompt => {
        const button = document.createElement('button');
        button.className = 'prompt-button';
        button.textContent = prompt.title;
        button.addEventListener('click', () => {
            messageInput.value = prompt.prompt;
            sendRequest();
            hidePromptIdeas();
        });
        promptsContainer.appendChild(button);
    });
    container.appendChild(promptsContainer);
    return promptsContainer;
}

function showPromptIdeas() {
    if (!promptIdeasVisible) {
        const existingIdeas = document.getElementById('promptIdeas');
        if (existingIdeas) existingIdeas.remove();
        
        createPromptIdeas();
        promptIdeasVisible = true;
    }
}

function hidePromptIdeas() {
    const container = document.getElementById('promptIdeasContainer');
    container.innerHTML = '';
    promptIdeasVisible = false;
}

showPromptIdeas();

function resetTextareaHeight() {
    const messageInput = document.getElementById('messageInput');
    messageInput.style.height = '38px'; // Reset to initial height
}

// Event listeners
sendButton.addEventListener('click', () => {
    if (messageInput.value.trim() === '' && pendingUploads.length === 0) return;
    sendRequest();
    hidePromptIdeas();
    resetTextareaHeight();
});

messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendButton.click();
    }
    // Automatically adjust height based on content
    // this.style.height = 'auto';
    // this.style.height = this.scrollHeight + 'px';
});

stopButton.addEventListener('click', () => {
    if (isGenerating && controller) {
        isGenerating = false;
        controller.abort();
        appendSystemMessage("Generation stopped by user.");
    }
});

newMessagesButton.addEventListener('click', () => {
    clearChatHistory();
    resetTextareaHeight();
    
    // Start a new conversation
    if (conversationManager) {
        conversationManager.startNewConversation();
    }
});

// Logout button event listeners (both desktop and mobile)
function handleLogout() {
    return async () => {
        try {
            await fetch(config.getEndpoints().logout, {
                method: 'POST',
                headers: {
                    ...getAuthHeaders()
                }
            });
        } catch (error) {
            console.error('Logout error:', error);
        } finally {
            logout();
        }
    };
}

const logoutButton = document.getElementById('logoutButton');
const logoutButtonMobile = document.getElementById('logoutButtonMobile');

if (logoutButton) {
    logoutButton.addEventListener('click', handleLogout());
}

if (logoutButtonMobile) {
    logoutButtonMobile.addEventListener('click', handleLogout());
}

// Error handling utility
function handleError(error, customMessage = 'An error occurred') {
    console.error(error);
    appendSystemMessage(`${customMessage}: ${error.message || 'Unknown error'}`);
}

// Safe DOM manipulation utility
function safeGetElement(id) {
    const element = document.getElementById(id);
    if (!element) {
        console.warn(`Element with id '${id}' not found`);
    }
    return element;
}

function buildAttachmentInstruction(attachments = []) {
    if (!attachments.length) return '';
    const basePath = `./static/{user_id}/${sessionId}/uploads`;
    const lines = attachments.map(att => {
        const relPath = att.path || att.storedName || att.name;
        const mimeType = att.mimeType ? ` (${att.mimeType})` : '';
        return `- ${att.name}${mimeType}${relPath ? ` | relative path: ${relPath}` : ''}`;
    }).join('\n');
    return `Files uploaded in this message:\nSession ID: ${sessionId}\nBase path: ${basePath}\n${lines}\nUse these paths when referencing the uploaded files.`;
}

function serializeMessagesForRequest(messageList = []) {
    return messageList.map(msg => {
        const { llmContent, attachments, userText, storageContent, ...rest } = msg;
        const serialized = { ...rest };
        if (llmContent) {
            serialized.content = llmContent;
        }
        return serialized;
    });
}

// Modify sendRequest to use better error handling
async function sendRequest(msgOverride=null) {
    const attachmentsToSend = pendingUploads.map(att => ({ ...att }));
    const rawInput = msgOverride !== null ? msgOverride : messageInput.value;
    const trimmedInput = rawInput ? rawInput.trim() : '';
    if (!trimmedInput && attachmentsToSend.length === 0) return;

    const attachmentSummaries = attachmentsToSend.map(att => ({
        name: att.name,
        path: att.path,
        size: att.size,
        mimeType: att.mimeType
    }));

    const llmInstruction = buildAttachmentInstruction(attachmentsToSend);
    const llmContentParts = [];
    if (trimmedInput) {
        llmContentParts.push(trimmedInput);
    }
    if (llmInstruction) {
        llmContentParts.push(llmInstruction);
    }
    const llmContent = llmContentParts.join('\n\n');

    const displaySegments = [];
    if (trimmedInput) {
        displaySegments.push(trimmedInput);
    }
    if (attachmentSummaries.length) {
        const attachmentLabel = formatAttachmentLabel(attachmentSummaries.length);
        const fileNames = attachmentSummaries.map(att => att.name).join(', ');
        displaySegments.push(`**${attachmentLabel}:** ${fileNames}`);
    }
    const displayContent = displaySegments.join('\n\n');

    try {
        // Input validation

        sendButton.disabled = true;
        stopButton.disabled = false;

        const userMessage = {
            id: generateId('msg'),
            role: 'user',
            type: 'message',
            content: displayContent || trimmedInput,
            userText: trimmedInput,
            attachments: attachmentSummaries,
            llmContent: llmContent || trimmedInput
        };
        messages.push(userMessage);
        appendMessage(userMessage);
        scrollToBottom();
        messageInput.value = '';
        pendingUploads = [];
        renderPendingUploads();

        showWorkingIndicator();

        // Define parameters for the POST request
        const params = {
            messages: serializeMessagesForRequest(messages)
        };

        // Initialize AbortController to handle cancellation
        controller = new AbortController();
        const { signal } = controller;

        // Send the POST request to the Python server endpoint with session ID header
        const interpreterCall = await fetch(config.getEndpoints().chat, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Session-Id": sessionId,
                ...getAuthHeaders()
            },
            body: JSON.stringify(params),
            signal,
        });

        // Throw an error if the request was not successful
        if (!interpreterCall.ok) {
            console.error("Interpreter didn't respond with 200 OK");
            if (interpreterCall.statusText) {
                appendSystemMessage(interpreterCall.statusText);
            } else {
                appendSystemMessage("Error: Unable to communicate with the server.");
            }
            resetButtons();
            return;
        }

        // Initialize a reader for the response body
        const reader = interpreterCall.body.getReader();
        const decoder = new TextDecoder("utf-8");

        isGenerating = true;

        let partialData = ''; // Buffer for partial data

        while (isGenerating) {
            const { value, done } = await reader.read();

            if (done) break;

            const text = decoder.decode(value, { stream: true });
            partialData += text;

            // Split the received text by newlines
            const lines = partialData.split("\n");

            // Keep the last line (it might be incomplete)
            partialData = lines.pop();

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    // console.log("Received line:", line);
                    const data = line.replace("data: ", "").trim();
                    // console.log("Received data:", data);
                    try {
                        const chunk = JSON.parse(data);
                        await processChunk(chunk);
                    } catch (e) {
                        console.error("Failed to parse chunk:", e);
                    }
                }
            }
        }

        resetButtons();
    } catch (error) {
        if (error.name === 'AbortError') {
            console.log("Request was aborted");
        } else {
            handleError(error, 'Failed to send request');
        }
    } finally {
        resetButtons();
    }
}

// Function to reset send and stop buttons
function resetButtons() {
    removeWorkingIndicator();
    sendButton.disabled = false;
    stopButton.disabled = true;
    controller = null;
    isGenerating = false;
}

function shouldStartNewBase64Image(message, chunk) {
    if (!message || message.type !== 'image') return false;
    const hasExistingContent = typeof message.content === 'string' && message.content.length > 0;
    if (!hasExistingContent) return false;

    const formatHint = chunk.format || message.format || '';
    if (!formatHint.startsWith('base64.')) return false;
    if (chunk.start) return false;

    const chunkContent = (chunk.content || '').trimStart();
    if (!chunkContent) return false;

    const pngHeader = 'iVBORw0KGgo';
    const jpegHeader = '/9j/';
    return chunkContent.startsWith(pngHeader) || chunkContent.startsWith(jpegHeader);
}

function saveCompletedAssistantMessage(message) {
    if (!conversationManager || !(message.role === 'assistant' || message.role === 'computer')) {
        return;
    }
    if (message.type === 'console') {
        return;
    }
    const validTypes = ['message', 'code', 'image', 'console', 'file', 'confirmation'];
    const messageType = validTypes.includes(message.type) ? message.type : 'message';

    conversationManager.addMessage(
        message.role,
        message.content,
        messageType,
        message.format,
        message.recipient
    ).catch(error => {
        console.error('Failed to save completed message to conversation:', error);
    });
}

function createImageMessageFromChunk(chunk, fallbackMessage) {
    return {
        id: generateId('msg'),
        role: chunk.role || (fallbackMessage && fallbackMessage.role) || 'assistant',
        type: chunk.type || (fallbackMessage && fallbackMessage.type) || 'image',
        content: '',
        format: chunk.format || (fallbackMessage && fallbackMessage.format) || undefined,
        recipient: chunk.recipient || (fallbackMessage && fallbackMessage.recipient) || undefined,
        created_at: new Date().toISOString(),
        isComplete: false,
    };
}

// Function to process each chunk of the stream and create messages
function processChunk(chunk) {
    chunk = normalizeIncomingChunk(chunk);
    return new Promise((resolve) => {
        removeWorkingIndicator();
        if (chunk.type === 'console' && chunk.format === 'active_line') {
            //console.log(chunk); // Debug log for active line chunks
            handleActiveLineChunk(chunk.content);
            resolve();
            return;
        }

        let message = null;

        if (chunk.start) {
            const newMessage = normalizeStdStreamMessage({
                id: generateId('msg'),
                role: chunk.role,
                type: chunk.type,
                content: chunk.content || '',
                format: chunk.format || undefined,
                recipient: chunk.recipient || undefined,
                created_at: new Date().toISOString(),
                isComplete: false,
            });
            messages.push(newMessage);
            appendMessage(newMessage);
            setActiveMessageId(chunk, newMessage.id);
            message = newMessage;
        } else if (chunk.error) {
            const errorMessage = chunk.error.message || chunk.error;
            appendSystemMessage(errorMessage);
            return;
        }

        if (!message) {
            const targetId = getActiveMessageId(chunk);
            if (targetId) {
                message = messages.find(msg => msg.id === targetId);
            }
        }

        if (message) {
            if (shouldStartNewBase64Image(message, chunk)) {
                message.isComplete = true;
                updateMessageContent(message.id, message.content);
                saveCompletedAssistantMessage(message);

                const newMessage = createImageMessageFromChunk(chunk, message);
                messages.push(newMessage);
                appendMessage(newMessage);
                setActiveMessageId(chunk, newMessage.id);
                message = newMessage;
            }

            if (chunk.end) {
                chunk.format = chunk.format || message.format || chunk.recipient || 'output';
                message.isComplete = true;
                saveCompletedAssistantMessage(message);
                clearActiveMessageId(chunk);
            }

            message.format = chunk.format || message.format || undefined;
            message.recipient = chunk.recipient || message.recipient || undefined;
            message.content += chunk.content || '';

            updateMessageContent(message.id, message.content);
        }

        resolve();
    });
}

// Function to append a message to the chat display, gets called on chunk start
function appendMessage(message) {
    // if (message.type == 'console'){
    //     // TODO: we should only skip console messages that have start or end properties
    //     // or have content that is empty
    //     return;
    // }
    const messageElement = document.createElement('div');
    messageElement.classList.add('message', message.role);
    if (message.id) {
        messageElement.setAttribute('data-id', message.id);
    }

    const contentElement = document.createElement('div');
    contentElement.classList.add('content');
    contentElement.setAttribute('data-type', message.type);
    if (message.role === 'user' && message.type === 'message') {
        const userContentWrapper = document.createElement('div');
        userContentWrapper.classList.add('user-message-wrapper');

        let attachmentNames = null;
        let textSource = typeof message.userText === 'string' ? message.userText : null;
        let parsedContentAttachments = null;

        if (Array.isArray(message.attachments) && message.attachments.length > 0) {
            attachmentLabel = formatAttachmentLabel(message.attachments.length);
            attachmentNames = message.attachments.map(att => att.name).join(', ');
        } else if (typeof message.content === 'string') {
            parsedContentAttachments = extractAttachmentInfoFromContent(message.content);
            if (parsedContentAttachments) {
                attachmentLabel = parsedContentAttachments.label || null;
                attachmentNames = parsedContentAttachments.names;
                if (textSource === null) {
                    textSource = parsedContentAttachments.remaining;
                }
            }
        }

        const fallbackText = parsedContentAttachments ? parsedContentAttachments.remaining : (message.content || '');
        const textToShow = (textSource !== null ? textSource : fallbackText || '').trim();
        if (textToShow) {
            const textBlock = document.createElement('div');
            textBlock.textContent = textToShow;
            userContentWrapper.appendChild(textBlock);
        }

        if (attachmentNames) {
            const attachmentLine = document.createElement('div');
            attachmentLine.className = 'user-attachment-line';
            const fallbackCount = (attachmentNames.match(/,/g) || []).length + 1;
            const label = attachmentLabel || formatAttachmentLabel(fallbackCount);
            attachmentLine.innerHTML = `<strong>${label}:</strong> ${escapeHtml(attachmentNames)}`;
            userContentWrapper.appendChild(attachmentLine);
        }

        contentElement.appendChild(userContentWrapper);
    } else if (message.type === 'image' && message.format === 'path') {
        const imageSrc = escapeHtml(message.content || '');
        const imageAlt = escapeHtml(message.filename || 'Uploaded image');
        contentElement.innerHTML = `<img src="${imageSrc}" alt="${imageAlt}" class="uploaded-image-preview">`;
    } else if (message.type === 'file') {
        const displayName = escapeHtml(message.filename || message.name || message.content || 'Attachment');
        const filePath = escapeHtml(message.content || '');
        contentElement.classList.add('file-attachment');
        contentElement.innerHTML = `
            <span class="material-icons attachment-icon">attach_file</span>
            <div class="attachment-details">
                <span class="attachment-name">${displayName}</span>
                <span class="attachment-path">${filePath}</span>
            </div>
        `;
    } else if (message.type === 'console') {
        contentElement.innerHTML = '<pre><code></code></pre>';
        messageElement.classList.add('console-output-message');
        contentElement.setAttribute('aria-hidden', 'true');
    } else {
        contentElement.innerHTML = message.content; 
    }

    messageElement.appendChild(contentElement);
    chatDisplay.appendChild(messageElement);
    chatDisplay.scrollTop = chatDisplay.scrollHeight;
    handleStdoutTrackingOnMessageStart(message);

    // Save user messages immediately to conversation (assistant/computer messages are saved when complete)
    if (conversationManager && message.role === 'user' && message.content) {
        // Validate message type against backend enums
        const validTypes = ['message', 'code', 'image', 'console', 'file', 'confirmation'];
        const messageType = validTypes.includes(message.type) ? message.type : 'message';
        
        // Save to conversation asynchronously
        conversationManager.addMessage(
            message.role, 
            message.content, 
            messageType, 
            message.format, 
            message.recipient
        ).catch(error => {
            console.error('Failed to save user message to conversation:', error);
        });
    }
}

// Modify updateMessageContent with better error handling
function handleActiveLineChunk(content) {
    if (!activeLineCodeId) {
        activeLineCodeId = lastExecutableCodeId || pendingConsoleParentId || null;
    }
    if (!activeLineCodeId) return;
    if (content) {
        isActiveLineRunning = true;
        renderActiveLineSpinner();
    } else {
        isActiveLineRunning = false;
        removeActiveLineSpinner();
        activeLineCodeId = null;
    }
}

function updateMessageContent(id, content) {
    try {
        const messageElement = chatDisplay.querySelector(`.message[data-id="${id}"]`);
        if (!messageElement) {
            throw new Error(`Message element with ID ${id} not found`);
        }

        const message = messages.find(msg => msg.id === id);
        if (!message) {
            throw new Error(`Message data with ID ${id} not found`);
        }

        const contentDiv = messageElement.querySelector('.content');
        if (!contentDiv) {
            throw new Error('Content div not found');
        }

        if (message.type === 'console') {
            if (isTelemetryConsoleMessage(message)) {
                return;
            }
            const pre = contentDiv.querySelector('pre code') || (() => {
                contentDiv.innerHTML = '<pre><code></code></pre>';
                return contentDiv.querySelector('pre code');
            })();
            if (pre) {
                pre.textContent = message.content || '';
            }
            const parent = contentDiv.parentElement;
            if (parent) {
                parent.classList.add('console-output-message');
                if (message.associatedCodeId) {
                    parent.setAttribute('data-associated-code-id', message.associatedCodeId);
                    refreshStdoutPanel(message.associatedCodeId, { autoScroll: true });
                }
            }
            contentDiv.setAttribute('aria-hidden', 'true');
            return;
        }
        
        // Handle different message types (more robust Math rendering)
        if (message.type === 'message') {
            // 1) Start from the raw content
            let raw = content;

            // 2) Protect any *closed* math from Markdown
            const { text: shielded, store } = protectMath(raw);

            // 3) If math is NOT balanced yet, avoid Markdown corruption.
            //    Show the raw text (or a very light markdown parse) and return early.
            //    This prevents Markdown from inserting tags inside $$... (which MathJax needs as plain text)
            if (!hasBalancedMath(raw)) {
            contentDiv.textContent = raw;   // no HTML injection; safe during streaming
            return;
            }

            // 4) Now it's safe: run Markdown, restore math, inject HTML
            const parsedMarkdown = marked.parse(shielded);
            const htmlWithMath = restoreMath(parsedMarkdown, store);
            contentDiv.innerHTML = htmlWithMath;

            // 5) Highlight code blocks using Prism
            prismHighlightUnder(contentDiv);

            // 6) Typeset math (don’t wait for message.isComplete)
            typeset(contentDiv);
        } else if (message.type === 'image') {
            if (message.format && message.format.startsWith('base64.')) {
                const mime = message.format.replace('base64.', 'image/');
                if (message.isComplete) {
                    contentDiv.innerHTML =
                        `<img src="data:${mime};base64,${content}" alt="Image">`;
                } else {
                    // still streaming, don't try to render partial base64
                    contentDiv.innerHTML = `<div class="image-placeholder"> Generating image… </div>`;
                }
            } else if (message.format === 'path') {
                // path-based images are usually already usable
                contentDiv.innerHTML = `<img src="${content}" alt="Image">`;
            }
        } else if (message.type === 'code') {
            const preservedStdoutState = captureStdoutPanelState(message.id);
            if (message.format === "html") {
                // contentDiv.innerHTML = content;
                // do nothing
                // return;
                // const sanitizedHtml = DOMPurify.sanitize(content);
                
                contentDiv.innerHTML = content;
                // return;
            } else {
                let codeBlock = contentDiv.querySelector('pre code');
                if (!codeBlock) {
                    contentDiv.innerHTML = `<pre><code class="language-${message.format || ''}"></code></pre>`;
                    codeBlock = contentDiv.querySelector('pre code');
                } else {
                    codeBlock.className = `language-${message.format || ''}`;
                }

                if (codeBlock) {
                    codeBlock.textContent = content;
                    Prism.highlightElement(codeBlock);
                }
                addCopyButtons();
                ensureStdoutElements(message.id);
                updateStdoutAvailability(message.id);
                restoreStdoutPanelState(message.id, preservedStdoutState);
                if (isActiveLineRunning && activeLineCodeId === message.id) {
                    renderActiveLineSpinner();
                }
            }
        } else if (message.type === 'file') {
            contentDiv.innerHTML = `<a href="${content}" download>Download File</a>`;
        } 
    } catch (error) {
        handleError(error, 'Failed to update message content');
    }
}

// Function to append system messages (like errors or notifications)
function appendSystemMessage(message) {
    const id = generateId('msg');
    const systemMessage = {
        id: id,
        role: 'system',
        type: 'system',
        content: message
    };
    messages.push(systemMessage);

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', 'system');
    messageElement.setAttribute('data-id', id);

    const content = document.createElement('div');
    content.classList.add('content');
    const parsedMarkdown = marked.parse(message);
    content.innerHTML = parsedMarkdown;
    content.querySelectorAll('pre code').forEach((block) => {
        Prism.highlightElement(block);
        // hljs.highlightElement(block);
    });

    messageElement.appendChild(content);
    chatDisplay.appendChild(messageElement);
    chatDisplay.scrollTop = chatDisplay.scrollHeight;
}

// Function to append confirmation chunks
function appendConfirmationChunk(chunk) {
    // Example: Show a prompt to the user to confirm code execution
    if (chunk.type === 'confirmation' && chunk.content) {
        const confirmation = chunk.content;
        const userConfirmed = confirm(`Execution Confirmation:\n\nType: ${confirmation.type}\nFormat: ${confirmation.format}\nContent:\n${confirmation.content}\n\nDo you want to proceed?`);

        if (userConfirmed) {
            // User confirmed, proceed with execution
            appendSystemMessage("Code execution confirmed.");
            // Optionally, send a confirmation back to the server if required
        } else {
            // User canceled, abort the generation
            isGenerating = false;
            if (controller) {
                controller.abort();
            }
            appendSystemMessage("Code execution canceled by user.");
        }
    }
}

// Function to clear chat history
async function clearChatHistory() {
    try {
        removeWorkingIndicator();
        // Clear chat history
        const response = await fetch(config.getEndpoints().clear, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Session-Id": sessionId,
                ...getAuthHeaders()
            },
        });

        if (!response.ok) {
            throw new Error("Failed to clear chat history");
        }

        // Clear uploaded files
        const fileResponse = await fetch(config.getEndpoints().files, {
            method: "DELETE",
            headers: {
                "X-Session-Id": sessionId,
                ...getAuthHeaders()
            },
        });

        if (!fileResponse.ok) {
            throw new Error("Failed to clear uploaded files");
        }

        // Clear frontend messages array
        messages = [];
        // Clear chat display
        isGenerating = false;
        controller = null;
        chatDisplay.innerHTML = '';
        resetStdoutState();
        
        // Clear uploaded files list in UI
        pendingUploads = [];
        renderPendingUploads();

        appendSystemMessage("Begin a new conversation.");
        showPromptIdeas();
        resetTextareaHeight();

    } catch (error) {
        console.error("An error occurred while clearing history:", error);
        appendSystemMessage("Error: Unable to clear history completely.");
    }
}

// Utility function to generate a unique ID for messages
function generateId(id_type) {
    return id_type + '-' + Math.random().toString(36).substr(2, 9);
}

// Utility function to escape HTML to prevent XSS in code blocks
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;',
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}

function formatAttachmentLabel(count) {
    return count === 1 ? 'File' : 'Files';
}

function extractAttachmentInfoFromContent(content) {
    if (typeof content !== 'string') return null;
    const lines = content.split('\n');
    for (let i = 0; i < lines.length; i++) {
        const trimmed = lines[i].trim();
        const match = trimmed.match(/^(?:\*\*)?(File|Files):(?:\*\*)?\s*(.+)$/i);
        if (match && i === 0) {
            const remaining = [...lines.slice(0, i), ...lines.slice(i + 1)].join('\n').trim();
            return {
                label: match[1].toLowerCase() === 'file' ? 'File' : 'Files',
                names: match[2].trim(),
                remaining
            };
        }
    }
    return null;
}

// Scroll to the bottom of the chat display
function scrollToBottom() {
    chatDisplay.scrollTop = chatDisplay.scrollHeight;
}

function showWorkingIndicator() {
    if (workingIndicatorId) {
        return workingIndicatorId;
    }

    workingIndicatorId = generateId('thinking');

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', 'assistant', 'thinking');
    messageElement.setAttribute('data-id', workingIndicatorId);

    const contentElement = document.createElement('div');
    contentElement.classList.add('content');
    contentElement.innerHTML = `
        <div class="thinking-content" role="status" aria-live="polite">
            <span class="thinking-spinner" aria-hidden="true"></span>
            <span>Thinking</span>
            <span class="thinking-ellipsis" aria-hidden="true">
                <span></span><span></span><span></span>
            </span>
        </div>
    `;

    messageElement.appendChild(contentElement);
    chatDisplay.appendChild(messageElement);
    scrollToBottom();

    return workingIndicatorId;
}

function removeWorkingIndicator() {
    if (!workingIndicatorId) return;

    const indicator = chatDisplay.querySelector(`.message[data-id="${workingIndicatorId}"]`);
    if (indicator) {
        indicator.remove();
    }

    workingIndicatorId = null;
}

function addCopyButtons() {
    // console.log("Adding copy buttons");
    const codeBlocks = document.querySelectorAll('pre code');
    
    codeBlocks.forEach((codeBlock) => {
        const pre = codeBlock.parentElement;

        // Avoid adding multiple buttons to the same code block
        if (pre.querySelector('.copy-button')) return;

        // Create the copy button
        const button = document.createElement('button');
        button.classList.add('copy-button');
        button.innerText = 'Copy';

        // Append the button to the <pre> element
        pre.appendChild(button);

        // Add click event to copy code
        button.addEventListener('click', () => {
            const code = codeBlock.innerText;
            navigator.clipboard.writeText(code).then(() => {
                button.innerText = 'Copied!';
                setTimeout(() => {
                    button.innerText = 'Copy';
                }, 2000);
            }).catch((err) => {
                console.error('Failed to copy code: ', err);
                button.innerText = 'Error';
                setTimeout(() => {
                    button.innerText = 'Copy';
                }, 2000);
            });
        });
    });
}

function resetStdoutState() {
    codeConsoleMap.clear();
    lastExecutableCodeId = null;
    pendingConsoleParentId = null;
    activeMessageIds.clear();
    activeLineCodeId = null;
    isActiveLineRunning = false;
    removeActiveLineSpinner();
}
window.resetStdoutState = resetStdoutState;

const STD_STREAM_RECIPIENTS = ['stdout', 'stderr'];

function normalizeStdStreamMessage(message) {
    if (!message) return message;
    if (!message.type && message.message_type) {
        message.type = message.message_type;
    }
    if (!message.format && message.message_format) {
        message.format = message.message_format;
    }
    if (!message.recipient && message.message_recipient) {
        message.recipient = message.message_recipient;
    }

    const recipient = (message.recipient || '').toLowerCase();
    if ((message.type === 'message' || message.type === 'text') && STD_STREAM_RECIPIENTS.includes(recipient)) {
        message.type = 'console';
        message.format = message.format || recipient;
    } else if (message.type === 'console' && !message.format && STD_STREAM_RECIPIENTS.includes(recipient)) {
        message.format = recipient;
    }
    return message;
}

function normalizeIncomingChunk(chunk) {
    if (!chunk) return chunk;
    if (chunk.recipient) {
        chunk.recipient = chunk.recipient.toLowerCase();
    }
    if ((chunk.type === 'message' || chunk.type === 'text') &&
        STD_STREAM_RECIPIENTS.includes(chunk.recipient || '')) {
        chunk.type = 'console';
        chunk.format = chunk.format || chunk.recipient;
    } else if (chunk.type === 'console' && !chunk.format && STD_STREAM_RECIPIENTS.includes(chunk.recipient || '')) {
        chunk.format = chunk.recipient;
    }
    if (chunk.type === 'console' && !chunk.format) {
        chunk.format = 'output';
    }
    return chunk;
}

function getChunkKey(chunk) {
    const role = chunk.role || '';
    const type = chunk.type || '';
    return `${role}:${type}`;
}

function getFormatKey(chunk) {
    if (!chunk) return '__default__';
    return chunk.format || chunk.recipient || '__default__';
}

function getFormatStore(baseKey) {
    if (!activeMessageIds.has(baseKey)) {
        activeMessageIds.set(baseKey, { map: new Map(), lastKey: null });
    }
    return activeMessageIds.get(baseKey);
}

function setActiveMessageId(chunk, messageId) {
    const baseKey = getChunkKey(chunk);
    const formatKey = getFormatKey(chunk);
    const store = getFormatStore(baseKey);
    store.map.set(formatKey, messageId);
    store.lastKey = formatKey;
}

function getActiveMessageId(chunk) {
    const baseKey = getChunkKey(chunk);
    const store = activeMessageIds.get(baseKey);
    if (!store) return null;
    const formatKey = getFormatKey(chunk);
    if (store.map.has(formatKey)) {
        return store.map.get(formatKey);
    }
    if (store.lastKey && store.map.has(store.lastKey)) {
        return store.map.get(store.lastKey);
    }
    const iterator = store.map.values().next();
    return iterator.value || null;
}

function getCodeMessageElement(codeId) {
    if (!codeId) return null;
    return chatDisplay.querySelector(`.message[data-id="${codeId}"]`);
}

function renderActiveLineSpinner() {
    if (!activeLineCodeId || !isActiveLineRunning) return;
    const messageElement = getCodeMessageElement(activeLineCodeId);
    if (!messageElement) return;
    const pre = messageElement.querySelector('pre');
    if (!pre) return;
    let spinner = pre.querySelector('.code-spinner');
    if (!spinner) {
        spinner = document.createElement('div');
        spinner.className = 'code-spinner';
        pre.appendChild(spinner);
    }
}

function removeActiveLineSpinner() {
    if (!activeLineCodeId) return;
    const messageElement = getCodeMessageElement(activeLineCodeId);
    if (!messageElement) return;
    const spinner = messageElement.querySelector('pre .code-spinner');
    if (spinner) {
        spinner.remove();
    }
}

function clearActiveMessageId(chunk) {
    const baseKey = getChunkKey(chunk);
    const store = activeMessageIds.get(baseKey);
    if (!store) return;
    const formatKey = getFormatKey(chunk);
    store.map.delete(formatKey);
    if (store.lastKey === formatKey) {
        const nextKey = store.map.keys().next();
        store.lastKey = nextKey.value || null;
    }
    if (store.map.size === 0) {
        activeMessageIds.delete(baseKey);
    }
}

function shouldTrackCodeMessage(message) {
    return Boolean(
        message &&
        message.type === 'code' &&
        message.role !== 'user' &&
        message.format !== 'html'
    );
}

function isConsoleOutputMessage(message) {
    if (!message) return false;
    const type = message.type || message.message_type;
    const format = message.format || message.message_format;
    const recipient = (message.recipient || message.message_recipient || '').toLowerCase();
    const isConsoleType = type === 'console' && format !== 'active_line' && !isTelemetryConsoleMessage(message);
    const isStdStream = (type === 'message' || type === 'text') && STD_STREAM_RECIPIENTS.includes(recipient);
    return isConsoleType || isStdStream;
}

function isTelemetryConsoleMessage(message) {
    const type = message?.type || message?.message_type;
    if (type !== 'console') return false;
    if (message.format === 'active_line') return true;
    const content = typeof message.content === 'string' ? message.content.trim() : '';
    if (message.format === 'execution' && /^\d+(?:\/\d+)?$/.test(content)) {
        return true;
    }
    if (/^line\s+\d+$/i.test(content)) {
        return true;
    }
    return false;
}

function markCodeMessageForStdout(message) {
    if (!message) return;
    if (activeLineCodeId && activeLineCodeId !== message.id) {
        removeActiveLineSpinner();
    }
    lastExecutableCodeId = message.id;
    pendingConsoleParentId = message.id;
    activeLineCodeId = message.id;
    isActiveLineRunning = false;
    if (!codeConsoleMap.has(message.id)) {
        codeConsoleMap.set(message.id, []);
    }
    ensureStdoutElements(message.id);
}

function addConsoleMapping(codeId, consoleId) {
    if (!codeId || !consoleId) return;
    if (!codeConsoleMap.has(codeId)) {
        codeConsoleMap.set(codeId, []);
    }
    const entries = codeConsoleMap.get(codeId);
    if (!entries.includes(consoleId)) {
        entries.push(consoleId);
    }
    updateStdoutAvailability(codeId);
}

function registerConsoleMessage(message) {
    if (!isConsoleOutputMessage(message)) return;
    let codeId = message.associatedCodeId || pendingConsoleParentId || lastExecutableCodeId;
    if (!codeId) {
        codeId = findPreviousExecutableCodeId(message.id);
    }
    if (!codeId) return;
    message.associatedCodeId = codeId;
    addConsoleMapping(codeId, message.id);
    pendingConsoleParentId = codeId;
    lastExecutableCodeId = codeId;
    refreshStdoutPanel(codeId, { autoScroll: true });
}

function handleStdoutTrackingOnMessageStart(message) {
    if (!message || message.__stdoutHandled) return;
    if (shouldTrackCodeMessage(message)) {
        markCodeMessageForStdout(message);
    } else if (isConsoleOutputMessage(message)) {
        registerConsoleMessage(message);
    } else if (isTelemetryConsoleMessage(message)) {
        // ignore telemetry chunks
    } else if (message.role === 'user') {
        pendingConsoleParentId = null;
        lastExecutableCodeId = null;
    }
    message.__stdoutHandled = true;
}

function ensureStdoutElements(codeId) {
    const messageElement = chatDisplay.querySelector(`.message[data-id="${codeId}"]`);
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
        button.type = 'button';
        button.className = 'stdout-button';
        button.textContent = 'Show Output';
        button.setAttribute('aria-expanded', 'false');
        button.disabled = true;
        button.addEventListener('click', () => toggleStdoutPanel(codeId));
        controls.appendChild(button);
        contentElement.appendChild(controls);
    }

    if (!panel) {
        panel = document.createElement('div');
        panel.className = 'stdout-panel';
        panel.setAttribute('role', 'region');
        panel.setAttribute('aria-label', 'STDOUT and STDERR output');
        panel.setAttribute('aria-hidden', 'true');
        contentElement.appendChild(panel);
    }

    return { messageElement, contentElement, controls, button, panel };
}

function updateStdoutAvailability(codeId) {
    const { controls, button, panel } = ensureStdoutElements(codeId);
    if (!controls || !button) {
        return;
    }
    const hasOutput = (codeConsoleMap.get(codeId) || []).length > 0;
    controls.classList.toggle('stdout-hidden', !hasOutput);
    button.disabled = !hasOutput;
    if (!hasOutput) {
        button.textContent = 'Show Output';
        button.setAttribute('aria-expanded', 'false');
        if (panel) {
            panel.classList.remove('open');
            panel.setAttribute('aria-hidden', 'true');
        }
    }
}

function getConsoleMessagesForCode(codeId) {
    const ids = codeConsoleMap.get(codeId) || [];
    return ids
        .map(id => messages.find(msg => msg.id === id))
        .filter(msg => Boolean(msg));
}

function findPreviousExecutableCodeId(referenceMessageId) {
    if (!referenceMessageId) return null;
    const index = messages.findIndex(msg => msg.id === referenceMessageId);
    if (index === -1) return null;
    for (let i = index - 1; i >= 0; i--) {
        const candidate = messages[i];
        if (shouldTrackCodeMessage(candidate)) {
            return candidate.id;
        }
    }
    return null;
}

function captureStdoutPanelState(codeId) {
    const messageElement = chatDisplay.querySelector(`.message[data-id="${codeId}"]`);
    if (!messageElement) return null;
    const panel = messageElement.querySelector('.stdout-panel');
    const button = messageElement.querySelector('.stdout-button');
    if (!panel || !button) return null;
    return {
        isOpen: panel.classList.contains('open')
    };
}

function restoreStdoutPanelState(codeId, state) {
    if (!state || !state.isOpen) return;
    const { panel, button } = ensureStdoutElements(codeId);
    if (!panel || !button || button.disabled) return;
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    button.textContent = 'Hide Output';
    button.setAttribute('aria-expanded', 'true');
    renderStdoutPanel(codeId);
}

function renderStdoutPanel(codeId) {
    const { panel } = ensureStdoutElements(codeId);
    if (!panel) return;
    panel.innerHTML = '';
    const outputs = getConsoleMessagesForCode(codeId).filter(msg => {
        const text = typeof msg?.content === 'string' ? msg.content : '';
        return text.trim().length > 0;
    });
    outputs.forEach(msg => {
        const entry = document.createElement('div');
        entry.className = 'stdout-entry';
        const pre = document.createElement('pre');
        pre.classList.add('stdout-pre');
        const code = document.createElement('code');
        code.classList.add('stdout-code');
        code.textContent = msg.content || '';
        pre.appendChild(code);
        entry.appendChild(pre);
        panel.appendChild(entry);
    });
    if (outputs.length === 0) {
        const emptyState = document.createElement('div');
        emptyState.className = 'stdout-empty';
        emptyState.textContent = 'No console output captured.';
        panel.appendChild(emptyState);
    }
}

function autoScrollStdoutPanel(panel) {
    if (!panel) return;
    panel.scrollTop = panel.scrollHeight;
    panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function toggleStdoutPanel(codeId) {
    const { button, panel } = ensureStdoutElements(codeId);
    if (!button || !panel || button.disabled) return;
    const isOpen = panel.classList.toggle('open');
    if (isOpen) {
        panel.setAttribute('aria-hidden', 'false');
        button.textContent = 'Hide Output';
        renderStdoutPanel(codeId);
        autoScrollStdoutPanel(panel);
    } else {
        panel.setAttribute('aria-hidden', 'true');
        button.textContent = 'Show Output';
    }
    button.setAttribute('aria-expanded', String(isOpen));
}

function refreshStdoutPanel(codeId, { autoScroll = false } = {}) {
    const { panel } = ensureStdoutElements(codeId);
    updateStdoutAvailability(codeId);
    if (!panel || !panel.classList.contains('open')) return;
    renderStdoutPanel(codeId);
    if (autoScroll) {
        autoScrollStdoutPanel(panel);
    }
}

// Fetch and display chat history on load
window.addEventListener('DOMContentLoaded', async () => {
    // Check authentication before doing anything else
    const isAuthenticated = await checkAuthentication();
    if (!isAuthenticated) {
        return; // Will redirect to login
    }

    if (!micStream) await warmUpMicrophone(); // Ensure microphone is warmed up (sppeds up first use)

    // Initialize conversation manager
    resetStdoutState();
    conversationManager = new ConversationManager();

    try {
        const response = await fetch(config.getEndpoints().history, {
            method: "GET",
            headers: {
                "X-Session-Id": sessionId,
                ...getAuthHeaders()
            }
        });
        if (response.ok) {
            const history = await response.json();
            if (history.length === 0) {
                showPromptIdeas();
            } else {
                history.forEach(message => {
                    normalizeStdStreamMessage(message);
                    if (message.type === 'console' && isTelemetryConsoleMessage(message)) {
                        return;
                    }
                    if (!message.id) {
                        message.id = generateId('msg');
                    }
                    messages.push(message);
                    appendMessage(message);
                    updateMessageContent(message.id, message.content);
                });
            }
            scrollToBottom();

            // Typeset once after conversation is loaded
            typeset(document.getElementById('chatDisplay'));
        }
    } catch (error) {
        console.error("Failed to fetch history:", error);
        showPromptIdeas();
    }
});

// This function sets all links to open in a new tab
function setLinksToNewTab() {
    document.querySelectorAll('a').forEach(link => {
      link.setAttribute('target', '_blank');
    });
  }
  
  // Call this function initially to set existing links
  setLinksToNewTab();
  
  // For dynamically created links, use a MutationObserver
  const observer = new MutationObserver(() => {
    setLinksToNewTab();
  });
  
  // Observe changes in the document body to catch dynamically added links
  observer.observe(document.body, { childList: true, subtree: true });


  window.onload = async function() {
    const urlParams = new URLSearchParams(window.location.search);
    const prompt = urlParams.get('prompt');
    
    if (prompt) {
        // Wait for select2 to be initialized
        await waitForSelect2();
        
        const inputField = document.getElementById('messageInput');
        if (inputField) {
            inputField.value = prompt;
            sendButton.click();
        }
    }
};

async function waitForSelect2(timeout = 5000) {
    return new Promise((resolve, reject) => {
        const startTime = Date.now();
        
        const checkSelect2 = () => {
            const select2Value = $('#myselect2').val();
            if (select2Value) {
                resolve();
            } else if (Date.now() - startTime > timeout) {
                reject(new Error('Timeout waiting for select2'));
            } else {
                setTimeout(checkSelect2, 100);
            }
        };
        
        checkSelect2();
    });
}

// File upload functionality (drag and drop, paste, etc.)
function initializeFileUpload() {
    const uploadButton = document.getElementById('uploadButton');
    const fileInput = document.getElementById('fileUpload');
    const uploadedFiles = document.getElementById('uploadedFiles');

    uploadButton.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', async () => {
        await handleFiles(fileInput.files);
        fileInput.value = '';
    });

    // Paste handler for screenshots
    document.addEventListener('paste', async (event) => {
        const items = event.clipboardData?.items;
        if (!items) return;

        for (const item of items) {
            if (item.type.startsWith('image')) {
                const originalFile = item.getAsFile();
                if (originalFile) {
                    const extension = originalFile.type.split('/')[1] || 'png';
                    const uniqueName = `pasted-${Date.now()}-${Math.floor(Math.random() * 1000)}.${extension}`;
                    const renamedFile = new File([originalFile], uniqueName, { type: originalFile.type });

                    if (progressBar) {
                        progressBar.style.display = 'block';
                    }
                    try {
                        const response = await uploadFile(renamedFile, progressElement);
                        queuePendingUpload(renamedFile, response);
                    } catch (error) {
                        appendSystemMessage(`Error uploading pasted image: ${error.message}`);
                    } finally {
                        if (progressBar) {
                            progressBar.style.display = 'none';
                        }
                    }
                }
            }
        }
    });

    updateFilesList();
}

// Mobile navigation functionality
function initializeMobileNavigation() {
    const navbarToggle = document.getElementById('navbarToggle');
    const navbarMobileMenu = document.getElementById('navbarMobileMenu');
    const mobileOverlay = document.getElementById('mobileOverlay');
    
    // Mobile menu buttons
    const downloadButtonMobile = document.getElementById('downloadButtonMobile');
    const newMessagesButtonMobile = document.getElementById('newMessagesButtonMobile');

    function toggleMobileMenu() {
        navbarToggle.classList.toggle('active');
        navbarMobileMenu.classList.toggle('active');
        mobileOverlay.classList.toggle('active');
        
        // Prevent body scroll when menu is open
        if (navbarMobileMenu.classList.contains('active')) {
            document.body.style.overflow = 'hidden';
        } else {
            document.body.style.overflow = '';
        }
    }

    function closeMobileMenu() {
        navbarToggle.classList.remove('active');
        navbarMobileMenu.classList.remove('active');
        mobileOverlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    // Toggle menu on hamburger click
    if (navbarToggle) {
        navbarToggle.addEventListener('click', toggleMobileMenu);
    }

    // Close menu on overlay click
    if (mobileOverlay) {
        mobileOverlay.addEventListener('click', closeMobileMenu);
    }

    // Handle mobile button clicks
    if (downloadButtonMobile) {
        downloadButtonMobile.addEventListener('click', () => {
            downloadConversation();
            closeMobileMenu();
        });
    }

    if (newMessagesButtonMobile) {
        newMessagesButtonMobile.addEventListener('click', () => {
            clearChatHistory();
            resetTextareaHeight();
            closeMobileMenu();
            
            // Start a new conversation
            if (conversationManager) {
                conversationManager.startNewConversation();
            }
        });
    }

    // Close menu on window resize if it gets too wide
    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            closeMobileMenu();
        }
    });

    // Close menu on escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && navbarMobileMenu.classList.contains('active')) {
            closeMobileMenu();
        }
    });
}

// File upload error handling improvements
async function uploadFile(file, progressElement) {
    try {
        if (!file) {
            throw new Error('No file provided');
        }

        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(config.getEndpoints().upload, {
            method: 'POST',
            headers: {
                'X-Session-Id': sessionId,
                ...getAuthHeaders()
            },
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Upload failed with status ${response.status}`);
        }

        const data = await response.json();
        return data;

    } catch (error) {
        handleError(error, `Failed to upload ${file.name}`);
        throw error; // Re-throw to handle in the calling function
    }
}

function updateFilesList() {
    renderPendingUploads();
}

async function downloadConversation() {
    try {
        appendSystemMessage("Preparing conversation for download...");
        
        // Create a complete, self-contained HTML document
        const htmlContent = await createSelfContainedHTML();
        
        // Create blob and download
        const blob = new Blob([htmlContent], { type: 'text/html;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        
        const a = document.createElement('a');
        a.href = url;
        a.download = `IDEA-conversation-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.html`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        appendSystemMessage("Conversation downloaded successfully!");
    } catch (err) {
        console.error("Download failed:", err);
        appendSystemMessage("Failed to download conversation. Please try again.");
    }
}

function shouldIncludeMessageInExport(message) {
    if (!message) return false;
    const msgType = message.type || message.message_type;
    if (msgType === 'console') {
        if (isTelemetryConsoleMessage(message)) {
            return false;
        }
        const text = typeof message.content === 'string' ? message.content.trim() : '';
        return text.length > 0;
    }
    return true;
}

function renderMessageContentForExport(message) {
    const msgType = message.type || message.message_type || 'message';
    const format = message.format || message.message_format || '';
    if (msgType === 'message') {
        const baseSource = message.content || message.userText || '';
        const rendered = marked ? marked.parse(baseSource) : baseSource;
        if (Array.isArray(message.attachments) && message.attachments.length > 0) {
            const alreadyPresent = /<strong>(?:file|files):<\/strong>/i.test(rendered);
            const attachmentList = message.attachments
                .map(att => escapeHtml(att.name))
                .join(', ');
            const label = formatAttachmentLabel(message.attachments.length);
            const prefix = alreadyPresent ? '' : `<p><strong>${label}:</strong> ${attachmentList}</p>`;
            return `${prefix}${rendered}`;
        }
        return rendered;
    }
    if (msgType === 'image') {
        if (format === 'base64.png') {
            return `<img src="data:image/png;base64,${message.content}" alt="Image">`;
        }
        return `<img src="${message.content}" alt="Image">`;
    }
    if (msgType === 'code') {
        if (format === 'html') {
            return message.content || '';
        }
        return `<pre><code class="language-${format || ''}">${escapeHtml(message.content || '')}</code></pre>`;
    }
    if (msgType === 'console') {
        return `<pre>${escapeHtml(message.content || '')}</pre>`;
    }
    if (msgType === 'file') {
        return `<a href="${message.content}" download>Download File</a>`;
    }
    return message.content || '';
}

function isExportCodeMessage(message) {
    if (!message) return false;
    const msgType = message.type || message.message_type;
    const format = message.format || message.message_format;
    return msgType === 'code' && format !== 'html' && message.role !== 'user';
}

function isExportConsoleOutput(message) {
    if (!message) return false;
    const msgType = message.type || message.message_type;
    const format = message.format || message.message_format;
    return msgType === 'console' && format !== 'active_line' && !isTelemetryConsoleMessage(message);
}

function buildStdoutAssociationsForExport(messageElements) {
    const map = new Map();
    let lastCodeId = null;
    messageElements.forEach(element => {
        const messageId = element.getAttribute('data-id');
        const messageData = getMessageDataForExport(messageId);
        if (!messageData) {
            return;
        }
        if (isExportCodeMessage(messageData)) {
            lastCodeId = messageId;
        } else if (isExportConsoleOutput(messageData)) {
            if (lastCodeId) {
                if (!map.has(lastCodeId)) {
                    map.set(lastCodeId, []);
                }
                map.get(lastCodeId).push({
                    id: messageId,
                    content: messageData.content || ''
                });
            }
        } else if ((messageData.type || messageData.message_type) !== 'console') {
            lastCodeId = null;
        }
    });
    return map;
}

function attachStdoutControlsForExport(contentElement, codeId, outputs) {
    if (!contentElement || !outputs || outputs.length === 0) return;
    const controls = document.createElement('div');
    controls.className = 'stdout-controls';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'stdout-button';
    button.textContent = 'Show Output';
    button.setAttribute('data-stdout-target', codeId);
    button.setAttribute('aria-expanded', 'false');
    controls.appendChild(button);
    contentElement.appendChild(controls);

    const panel = document.createElement('div');
    panel.className = 'stdout-panel';
    panel.setAttribute('data-stdout-target', codeId);
    panel.setAttribute('aria-hidden', 'true');
    panel.innerHTML = outputs.map(item => `
        <div class="stdout-entry">
            <pre class="stdout-pre"><code class="stdout-code">${escapeHtml(item.content || '')}</code></pre>
        </div>
    `).join('');
    contentElement.appendChild(panel);
}

function getMessageDataForExport(messageId) {
    if (!messageId) return null;
    
    if (Array.isArray(messages)) {
        const inSession = messages.find(msg => msg.id === messageId);
        if (inSession) {
            return inSession;
        }
    }
    
    if (conversationManager && Array.isArray(conversationManager.currentMessages)) {
        const fromConversation = conversationManager.currentMessages.find(msg => msg.id === messageId);
        if (fromConversation) {
            return fromConversation;
        }
    }
    
    return null;
}

function prepareChatCloneForExport() {
    const chatClone = chatDisplay.cloneNode(true);
    const messageElements = Array.from(chatClone.querySelectorAll('.message'));
    const stdoutAssociations = buildStdoutAssociationsForExport(messageElements);
    
    messageElements.forEach(element => {
        const messageId = element.getAttribute('data-id');
        const messageData = getMessageDataForExport(messageId);
        if (!messageData) {
            return;
        }
        
        if (!shouldIncludeMessageInExport(messageData)) {
            element.remove();
            return;
        }
        
        const contentEl = element.querySelector('.content');
        if (!contentEl) {
            return;
        }
        
        contentEl.setAttribute('data-type', messageData.type || messageData.message_type || 'message');
        contentEl.innerHTML = renderMessageContentForExport(messageData);
        
        if (isExportConsoleOutput(messageData)) {
            element.classList.add('console-output-message');
        }

        const outputs = stdoutAssociations.get(messageId);
        if (outputs && outputs.length && isExportCodeMessage(messageData)) {
            attachStdoutControlsForExport(contentEl, messageId, outputs);
        }
    });
    
    if (window.Prism && Prism.highlightAllUnder) {
        Prism.highlightAllUnder(chatClone);
    }
    
    return chatClone;
}

async function createSelfContainedHTML() {
    // Get all CSS from the current page
    const allCSS = await extractAllCSS();
    
    // Prepare export chat content
    const exportChat = prepareChatCloneForExport();
    await processImagesInElement(exportChat);
    
    const generatedOn = new Date();
    const generatedDate = generatedOn.toLocaleDateString();
    const generatedTimestamp = generatedOn.toLocaleString();
    const downloadedDisplay = generatedOn.toLocaleString(undefined, {
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: 'numeric',
        hour12: true,
        timeZoneName: 'short'
    });
    
    // Create the complete HTML document
    const htmlTemplate = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IDEA Conversation - ${generatedDate}</title>
    <style>
        ${allCSS}
        
        body.export-view {
            background: var(--body-gradient);
            min-height: 100vh;
            padding: clamp(16px, 5vw, 40px);
            overflow-y: auto !important;
            overflow-x: hidden;
        }

        .export-view .chat-container {
            height: auto;
            max-height: none;
        }

        .export-view .chat-display {
            max-height: none;
            overflow: visible;
        }

        .export-chat-panel {
            flex: 1;
            display: flex;
            flex-direction: column;
            padding: clamp(18px, 4vw, 32px);
            background: var(--surface-alt);
            border-top: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
        }

        .export-chat-panel .chat-display {
            width: 100%;
            min-width: 0;
            flex: 1;
        }

        .export-view .disclaimer-text {
            position: static;
            margin: 0 auto;
            width: min(1200px, 100%);
        }

        .export-header .header-content {
            justify-content: space-between;
            align-items: flex-start;
            gap: clamp(12px, 3vw, 24px);
        }

        .export-view .message .content pre {
            background: rgba(1, 4, 5, 0.9);
            color: #e2e8f0;
            padding: 14px;
            border-radius: 12px;
            overflow-x: auto;
            position: relative;
        }

        body.theme-light.export-view .message .content pre {
            background: #0f172a;
        }

        .export-view .message .content code {
            font-family: 'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace;
            font-size: 0.92em;
        }

        .export-meta {
            display: flex;
            flex-direction: column;
            gap: 4px;
            text-align: right;
            color: rgba(255, 255, 255, 0.72);
        }

        .export-title {
            font-size: 0.95rem;
        }

        .export-meta-text {
            font-size: 0.9rem;
            color: rgba(255, 255, 255, 0.7);
        }

        .export-brand-link {
            color: rgba(255, 255, 255, 0.72);
            text-decoration: underline;
        }

        .export-brand-link:visited {
            color: rgba(255, 255, 255, 0.72);
        }

        .export-footer {
            margin-top: 18px;
            text-align: center;
            color: var(--text-muted);
            font-size: 0.9rem;
        }

        .export-footer p {
            margin: 0.25rem 0;
        }

        @media print {
            body.export-view {
                padding: 0;
            }

            .chat-container {
                box-shadow: none !important;
            }

            .export-chat-panel {
                padding: 18px 24px;
            }

            .export-footer {
                display: block !important;
                margin-top: 16px;
                font-size: 0.85rem;
                color: #444;
            }
        }
    </style>
</head>
<body class="main-app theme-light export-view">
    <div class="app">
        <div class="chat-container export-chat-container">
            <header class="chat-header export-header">
                <div class="header-content">
                    <div class="header-brand">
                        <span class="brand-abbrev">IDEA</span>
                        <a class="brand-name export-brand-link" href="https://uhslc.soest.hawaii.edu/research/IDEA" target="_blank" rel="noreferrer noopener">Intelligent Data Exploring Assistant</a>
                    </div>
                    <div class="export-meta">
                        <span class="export-title">Downloaded conversation</span>
                        <span class="export-meta-text">${downloadedDisplay}</span>
                        <span class="export-meta-text">(Equation rendering requires internet.)</span>
                    </div>
                </div>
            </header>
            
            <div class="export-chat-panel">
                <div class="chat-display">
                    ${exportChat.innerHTML}
                </div>
            </div>
        </div>
        <div class="export-footer">
            <p>
                IDEA can make mistakes — check important results.
                <a href="https://github.com/uhsealevelcenter/IDEA" target="_blank" rel="noreferrer noopener">[More info on GitHub]</a>
            </p>
        </div>
    </div>
    
    <script>
        window.MathJax = {
            tex: {
                inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
                processEscapes: true,
                processEnvironments: true
            },
            options: {
                skipHtmlTags: ['script', 'noscript', 'style', 'textarea']
            },
            svg: { fontCache: 'global' }
        };
    </script>
    <script id="MathJax-script" defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
    <script>
        // Add some interactivity for the exported file
        document.addEventListener('DOMContentLoaded', function() {
            // Make all links open in new tab
            document.querySelectorAll('a').forEach(link => {
                if (!link.getAttribute('target')) {
                    link.setAttribute('target', '_blank');
                }
            });
            
            // Add click-to-copy functionality for code blocks
            document.querySelectorAll('pre code').forEach(function(codeBlock) {
                const pre = codeBlock.parentElement;
                if (!pre.querySelector('.export-copy-btn')) {
                    const copyBtn = document.createElement('button');
                    copyBtn.className = 'export-copy-btn';
                    copyBtn.innerHTML = 'Copy';
                    copyBtn.style.cssText = \`
                        position: absolute;
                        top: 8px;
                        right: 8px;
                        background: #007bff;
                        color: white;
                        border: none;
                        border-radius: 4px;
                        padding: 4px 8px;
                        font-size: 12px;
                        cursor: pointer;
                        opacity: 0.8;
                    \`;
                    
                    pre.style.position = 'relative';
                    pre.appendChild(copyBtn);
                    
            copyBtn.addEventListener('click', function() {
                navigator.clipboard.writeText(codeBlock.textContent).then(function() {
                    copyBtn.innerHTML = 'Copied!';
                    setTimeout(function() {
                        copyBtn.innerHTML = 'Copy';
                            }, 2000);
                        }).catch(function() {
                            // Fallback for older browsers
                            const textarea = document.createElement('textarea');
                            textarea.value = codeBlock.textContent;
                            document.body.appendChild(textarea);
                            textarea.select();
                            document.execCommand('copy');
                            document.body.removeChild(textarea);
                            copyBtn.innerHTML = 'Copied!';
                            setTimeout(function() {
                                copyBtn.innerHTML = 'Copy';
                            }, 2000);
                        });
                    });
                }
            });

            document.querySelectorAll('.stdout-button').forEach(function(button) {
                button.addEventListener('click', function() {
                    const targetId = button.getAttribute('data-stdout-target');
                    if (!targetId) return;
                    const selector = '.stdout-panel[data-stdout-target="' + targetId + '"]';
                    const panel = document.querySelector(selector);
                    if (!panel) return;
                    const isOpen = panel.classList.toggle('open');
                    panel.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
                    button.textContent = isOpen ? 'Hide Output' : 'Show Output';
                    button.setAttribute('aria-expanded', String(isOpen));
                    if (isOpen) {
                        panel.scrollTop = panel.scrollHeight;
                        panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                    }
                });
            });

            const typesetMath = () => {
                if (window.MathJax && MathJax.typesetPromise) {
                    MathJax.typesetPromise().catch(err => console.warn('MathJax typeset error:', err));
                } else if (!(window.MathJax && MathJax.typesetPromise)) {
                    setTimeout(typesetMath, 150);
                }
            };
            typesetMath();
        });
    </script>
</body>
</html>`;
    
    return htmlTemplate;
}

async function extractAllCSS() {
    let allCSS = '';
    
    // Extract CSS from style tags
    document.querySelectorAll('style').forEach(style => {
        allCSS += style.textContent + '\n';
    });
    
    // Extract CSS from external stylesheets
    const styleSheets = Array.from(document.styleSheets);
    for (const sheet of styleSheets) {
        try {
            if (sheet.href && sheet.href.startsWith(window.location.origin)) {
                // Only process same-origin stylesheets
                const cssRules = Array.from(sheet.cssRules || sheet.rules || []);
                cssRules.forEach(rule => {
                    allCSS += rule.cssText + '\n';
                });
            }
        } catch (e) {
            // Cross-origin stylesheets can't be read, skip them
            console.warn('Could not read stylesheet:', sheet.href);
        }
    }
    
    return allCSS;
}

async function processImagesInElement(element) {
    const images = element.querySelectorAll('img');
    
    for (const img of images) {
        try {
            // Only process images that are not already data URLs
            if (!img.src.startsWith('data:')) {
                const dataURL = await convertImageToDataURL(img);
                if (dataURL) {
                    img.src = dataURL;
                }
            }
        } catch (e) {
            console.warn('Could not convert image to data URL:', img.src);
        }
    }
}

function convertImageToDataURL(img) {
    return new Promise((resolve) => {
        try {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            
            // Create a new image to handle cross-origin issues
            const newImg = new Image();
            newImg.crossOrigin = 'anonymous';
            
            newImg.onload = function() {
                canvas.width = newImg.naturalWidth;
                canvas.height = newImg.naturalHeight;
                ctx.drawImage(newImg, 0, 0);
                
                try {
                    const dataURL = canvas.toDataURL('image/png');
                    resolve(dataURL);
                } catch (e) {
                    console.warn('Could not convert image to data URL:', e);
                    resolve(null);
                }
            };
            
            newImg.onerror = function() {
                console.warn('Could not load image for conversion');
                resolve(null);
            };
            
            newImg.src = img.src;
        } catch (e) {
            console.warn('Error in convertImageToDataURL:', e);
            resolve(null);
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initializeFileUpload();
    initializeMobileNavigation();

    // Microphone Dictation Button Logic
    const micButton = document.getElementById('micButton');
    const micIcon = micButton.querySelector('i');
    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;

    micButton.addEventListener('click', async () => {
        if (!isRecording) {
            await startRecording();
        } else {
            stopRecording();
        }
    });

    async function startRecording() {
        try {
            const stream = micStream || await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream, {
                mimeType: 'audio/webm;codecs=opus'
            });

            audioChunks = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) audioChunks.push(event.data);
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
                await uploadAudioBlob(audioBlob);
            };

            mediaRecorder.start();
            micButton.classList.add('recording');
            micIcon.textContent = "stop";  // Changes icon from "mic" to "stop"
            isRecording = true;
        } catch (err) {
            console.error("Microphone error:", err);
            appendSystemMessage("Microphone not available or permission denied.");
            micButton.classList.remove('recording');
            micIcon.textContent = "mic";
            isRecording = false;
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state !== "inactive") {
            mediaRecorder.stop();
        }
        micButton.classList.remove('recording');
        micIcon.textContent = "mic";
        isRecording = false;
    }

    async function uploadAudioBlob(blob) {
        const formData = new FormData();
        formData.append('file', blob, 'recording.webm');

        try {
            const response = await fetch(config.getEndpoints().transcribe, {
                method: 'POST',
                body: formData
            });

            if (response.ok) {
                const data = await response.json();
                messageInput.value = data.text;
            } else {
                appendSystemMessage("Transcription failed.");
            }
        } catch (err) {
            console.error("Upload error:", err);
            appendSystemMessage("Error sending audio for transcription.");
        }
    }

    // Drag overlay logic
    const dropOverlay = document.getElementById('dropOverlay');
    if (!dropOverlay) {
        console.warn('⚠️ dropOverlay element not found in DOM.');
        return;
    }

    let dragTimer;

    document.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropOverlay.classList.add('show');
        clearTimeout(dragTimer);
        dragTimer = setTimeout(() => {
            dropOverlay.classList.remove('show');
        }, 150);
    });

    document.addEventListener('dragleave', () => {
        dropOverlay.classList.remove('show');
    });

    document.addEventListener('drop', async (e) => {
        e.preventDefault();
        dropOverlay.classList.remove('show');

        // Check if drop is happening within knowledge base modal
        const knowledgeBaseModal = document.getElementById('knowledgeBaseModal');
        if (knowledgeBaseModal && knowledgeBaseModal.style.display === 'block') {
            const modalRect = knowledgeBaseModal.getBoundingClientRect();
            if (e.clientX >= modalRect.left && e.clientX <= modalRect.right &&
                e.clientY >= modalRect.top && e.clientY <= modalRect.bottom) {
                // Drop is within knowledge base modal, let it handle the event
                return;
            }
        }

        if (e.dataTransfer?.files?.length > 0) {
            await handleFiles(e.dataTransfer.files);
        }
    });

    // Add download button event listener
    const downloadButton = document.getElementById('downloadButton');
    if (downloadButton) {
        downloadButton.addEventListener('click', downloadConversation);
    }
});

messageInput.addEventListener('input', function() {
    // Reset height to auto to get correct scrollHeight
    this.style.height = 'auto';
    // Set new height based on content
    this.style.height = Math.min(this.scrollHeight, 200) + 'px';
});
