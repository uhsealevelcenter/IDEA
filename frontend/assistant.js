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
let currentMessageId = null;
let workingIndicatorId = null;

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
const themeToggleInputs = document.querySelectorAll('[data-theme-toggle]');
const THEME_STORAGE_KEY = 'idea-theme';

async function handleFiles(files) {
    if (!files || files.length === 0) return;

    hidePromptIdeas(); 
    progressBar.style.display = 'block';
    for (const file of files) {
        try {
            const response = await uploadFile(file, progressElement);
            const imagePath = response.path;

            if (file.type.startsWith('image')) {
                // Show the image inline
                const imageMessage = {
                    id: generateId('msg'),
                    role: 'user',
                    type: 'image',
                    format: 'path',
                    content: imagePath,
                    isComplete: true
                };
                // Append image visually and store in conversation
                //appendMessage(imageMessage); // Commenting this out to avoid double messages
                messages.push(imageMessage);
                scrollToBottom();

                // Send clean user message about the upload
                await sendRequest(`Please describe this image that I uploaded (${file.name})`);
            } else {
                sendRequest(`I uploaded ${file.name}`);
            }
        } catch (error) {
            appendSystemMessage(`Error uploading ${file.name}: ${error.message}`);
        }
    }
    progressBar.style.display = 'none';
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
    if (messageInput.value.trim() === '') return;
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

// Modify sendRequest to use better error handling
async function sendRequest(msgOverride=null) {
    const userInput = msgOverride ? msgOverride : messageInput.value.trim();
    if (!userInput) return;

    try {
        // Input validation

        sendButton.disabled = true;
        stopButton.disabled = false;

        const userMessage = {
            id: generateId('msg'),
            role: 'user',
            type: 'message',
            content: userInput
        };
        messages.push(userMessage);
        appendMessage(userMessage);
        scrollToBottom();
        messageInput.value = '';

        showWorkingIndicator();

        // Define parameters for the POST request
        const params = {
            messages
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
    let messageStarted = false;
    let messageEnded = false;
    return new Promise((resolve) => {
        removeWorkingIndicator();
        if (chunk.start) {
            // Start of a new message
            const newMessage = {
                id: generateId('msg'),
                role: chunk.role,
                type: chunk.type,
                content: chunk.content || '',
                format: chunk.format || undefined,
                recipient: chunk.recipient || undefined,
                created_at: new Date().toISOString(),
                isComplete: false,
            };
            messages.push(newMessage);
            appendMessage(newMessage);
            currentMessageId = newMessage.id; // Use ID instead of index
        }else{
            if (chunk.error) {
                // if chunk error has message
                const errorMessage = chunk.error.message || chunk.error;
    appendSystemMessage(errorMessage);
                return;
            }
        }

        // if (chunk.end) {
        //     // End of the current message
        //     resolve();
        //     return;
        // }

        // Append content to the message with the current ID
        let message = messages.find(msg => msg.id === currentMessageId);
        if (message) {
            if (shouldStartNewBase64Image(message, chunk)) {
                message.isComplete = true;
                updateMessageContent(message.id, message.content);
                saveCompletedAssistantMessage(message);

                const newMessage = createImageMessageFromChunk(chunk, message);
                messages.push(newMessage);
                appendMessage(newMessage);
                currentMessageId = newMessage.id;
                message = newMessage;
            }

            if (chunk.end) {
                message.isComplete = true;  // Mark message as complete
                // console.log(`Message ${currentMessageId} completed`);

                // Save the completed message to conversation if it's from assistant or computer
                saveCompletedAssistantMessage(message);
            }
            message.format = chunk.format || message.format || undefined;
            message.recipient = chunk.recipient || message.recipient || undefined;
            if (chunk.format == 'active_line') {
                message.content = chunk.content || '';
            }else{
                message.content += chunk.content || '';
            }
            
            // Update the displayed message
            updateMessageContent(currentMessageId, message.content);
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
    contentElement.innerHTML = message.content; 

    messageElement.appendChild(contentElement);
    chatDisplay.appendChild(messageElement);
    chatDisplay.scrollTop = chatDisplay.scrollHeight;

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

function appendExternalMessage({ role = 'assistant', content = '', type = 'message', format = null, recipient = null }) {
    if (!content || !chatDisplay) return;
    const id = generateId('msg');
    const message = {
        id,
        role,
        content,
        type,
        format,
        recipient,
        isComplete: true,
    };
    messages.push(message);
    appendMessage(message);
    try {
        updateMessageContent(id, content);
    } catch (err) {
        console.warn('Unable to render external message:', err);
    }

    if (conversationManager) {
        const validTypes = ['message', 'code', 'image', 'console', 'file', 'confirmation'];
        const messageType = validTypes.includes(type) ? type : 'message';
        conversationManager
            .addMessage(role, content, messageType, format, recipient)
            .catch((error) => {
                console.error('Failed to persist external message:', error);
            });
    }
}

window.appendExternalMessage = appendExternalMessage;

// Modify updateMessageContent with better error handling
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

        if (message.type === 'console' && message.format === 'active_line') {
            // console.log("Active line MESSAGE", message);
            // console.log("Active line CONTENT", content);
            // Find the most recent code block
            const codeBlocks = document.querySelectorAll('pre code');
            const lastCodeBlock = codeBlocks[codeBlocks.length - 1];
            
            if (lastCodeBlock) {
                // if (content) {
                //     console.log('Current line being executed:', content);
                //     // ... rest of your line highlighting code ...
                // } else {
                //     console.log('Execution completed!');
                // }
                const existingSpinner = lastCodeBlock.parentElement.querySelector('.code-spinner');
                    if (existingSpinner) {
                        existingSpinner.remove();
                    }

                    if (content) {
                        // Add spinner
                        const spinner = document.createElement('div');
                        spinner.className = 'code-spinner';
                        lastCodeBlock.parentElement.appendChild(spinner);

                        // // Highlight current line
                        // const lines = lastCodeBlock.innerHTML.split('\n');
                        // const highlightedLines = lines.map((line, index) => {
                        //     const lineNumber = index + 1;
                        //     const isActive = lineNumber === parseInt(content);
                        //     return `<div class="code-line${isActive ? ' active-line' : ''}">${line}</div>`;
                        // }).join('');
                        // lastCodeBlock.innerHTML = highlightedLines;
                    } else {
                        // Remove highlights
                        // const lines = lastCodeBlock.innerHTML.split('\n');
                        // const unhighlightedLines = lines.map(line => 
                        //     `<div class="code-line">${line}</div>`
                        // ).join('');
                        // lastCodeBlock.innerHTML = unhighlightedLines;
                    }
            }
            return;
        }

        if (contentDiv.getAttribute('data-type') === 'console') {
            // contentDiv.parentElement.remove();
            contentDiv.parentElement.style.display = 'none'; // Hide Console Output
            return;
        }
        
        // Handle different message types (more robust Math rendering)
        if (message.format === 'tool_status') {
            // Render a compact tool-status line with spinner/check
            const isDone = !!message.isComplete;
            const text = content || message.content || '';
            const statusHtml = `
                <div class="tool-status">
                    <span class="${isDone ? 'tool-check' : 'thinking-spinner'}" aria-hidden="true"></span>
                    <span class="tool-status-text">${escapeHtml(text)}</span>
                </div>
            `;
            contentDiv.innerHTML = statusHtml;
            return;
        } else if (message.type === 'message') {
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
            if (message.format === "html") {
                // contentDiv.innerHTML = content;
                // do nothing
                // return;
                // const sanitizedHtml = DOMPurify.sanitize(content);
                
                contentDiv.innerHTML = content;
                // return;
            } else 
            {
            contentDiv.innerHTML = `<pre><code class="language-${message.format || ''}">${escapeHtml(content)}</code></pre>`;
            // Apply syntax highlighting
            const codeBlock = contentDiv.querySelector('code');
            if (codeBlock ) {
                // hljs.highlightElement(codeBlock);
                Prism.highlightElement(codeBlock);
            }
            addCopyButtons();
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
        currentMessageId = null;
        isGenerating = false;
        controller = null;
        chatDisplay.innerHTML = '';
        
        // Clear uploaded files list in UI
        const uploadedFiles = document.getElementById('uploadedFiles');
        if (uploadedFiles) {
            uploadedFiles.innerHTML = '';
        }

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

// Fetch and display chat history on load
window.addEventListener('DOMContentLoaded', async () => {
    // Check authentication before doing anything else
    const isAuthenticated = await checkAuthentication();
    if (!isAuthenticated) {
        return; // Will redirect to login
    }

    if (!micStream) await warmUpMicrophone(); // Ensure microphone is warmed up (sppeds up first use)

    // Initialize conversation manager
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
            history.forEach(message => {
                // Assign an id if it's missing

                if (history.length === 0) {
                    showPromptIdeas();
                } else if (message.type != 'console') {
                    if (!message.id) {
                        message.id = generateId('msg');
                    }
                    messages.push(message); // Add to messages array
                    appendMessage(message);
                    updateMessageContent(message.id, message.content);
                }
            });
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

                    progressBar.style.display = 'block';
                    try {
                        const response = await uploadFile(renamedFile, progressElement);
                        const imagePath = response.path;

                        // Show the image inline
                        const imageMessage = {
                            id: generateId('msg'),
                            role: 'user',
                            type: 'image',
                            format: 'path',
                            content: imagePath,
                            isComplete: true
                        };
                        //appendMessage(imageMessage); // Commenting this out to avoid double messages
                        messages.push(imageMessage);
                        scrollToBottom();

                        await sendRequest(`Please describe this screenshot that I uploaded (${renamedFile.name})`);
                    } catch (error) {
                        appendSystemMessage(`Error uploading pasted image: ${error.message}`);
                    } finally {
                        progressBar.style.display = 'none';
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
        appendSystemMessage(`Successfully uploaded ${file.name}`);
        //sendRequest(`I uploaded ${file.name}`); // Commenting this out to avoid double messages
        return data;

    } catch (error) {
        handleError(error, `Failed to upload ${file.name}`);
        throw error; // Re-throw to handle in the calling function
    }
}

async function updateFilesList() {
    try {
        const response = await fetch(config.getEndpoints().files, {
            headers: {
                'X-Session-Id': sessionId,
                ...getAuthHeaders()
            }
        });

        if (!response.ok) {
            throw new Error('Failed to fetch files list');
        }

        const files = await response.json();
        const uploadedFiles = document.getElementById('uploadedFiles');
        uploadedFiles.innerHTML = '';

        files.forEach(file => {
            const fileElement = document.createElement('div');
            fileElement.className = 'uploaded-file';
            fileElement.innerHTML = `
                <span>${file.name} (${formatFileSize(file.size)})</span>
                <button class="delete-file" data-filename="${file.name}">×</button>
            `;

            // Add delete button event listener
            const deleteButton = fileElement.querySelector('.delete-file');
            deleteButton.addEventListener('click', async () => {
                try {
                    const filename = deleteButton.getAttribute('data-filename');
                    const response = await fetch(`${config.getEndpoints().files}/${encodeURIComponent(filename)}`, {
                        method: 'DELETE',
                        headers: {
                            'X-Session-Id': sessionId,
                            ...getAuthHeaders()
                        }
                    });

                    if (!response.ok) {
                        const error = await response.json();
                        throw new Error(error.detail || 'Delete failed');
                    }

                    // Remove the file element from the UI
                    fileElement.remove();
                    appendSystemMessage(`Successfully deleted ${filename}`);
                } catch (error) {
                    appendSystemMessage(`Error deleting file: ${error.message}`);
                }
            });

            uploadedFiles.appendChild(fileElement);
        });
    } catch (error) {
        console.error('Error updating files list:', error);
    }
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
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

async function createSelfContainedHTML() {
    // Get all CSS from the current page
    const allCSS = await extractAllCSS();
    
    // Clone the chat display and process images
    const chatClone = chatDisplay.cloneNode(true);
    await processImagesInElement(chatClone);
    
    // Create the complete HTML document
    const htmlTemplate = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IDEA Conversation - ${new Date().toLocaleDateString()}</title>
    <style>
        ${allCSS}
        
        /* Additional styles for the exported conversation */
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #fff;
            overflow-y: auto !important;
            overflow-x: hidden;
        }
        
        .export-header {
            text-align: center;
            margin-bottom: 40px;
            border-bottom: 2px solid #e0e0e0;
            padding-bottom: 20px;
        }
        
        .export-header h1 {
            color: #2c3e50;
            margin-bottom: 10px;
        }
        
        .export-header p {
            color: #7f8c8d;
            margin: 5px 0;
        }
        
        .chat-display {
            max-height: none !important;
            overflow: visible !important;
        }
        
        /* Ensure code blocks are properly styled */
        pre {
            background-color: #000000 !important;
            border-radius: 6px !important;
            padding: 16px !important;
            overflow-x: auto !important;
            white-space: pre-wrap !important;
            word-wrap: break-word !important;
        }
        
        code {
            font-family: 'Courier New', Courier, monospace !important;
            font-size: 0.9em !important;
        }
        
        /* Ensure images are responsive */
        img {
            max-width: 100% !important;
            height: auto !important;
            border-radius: 8px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1) !important;
        }
        
        /* Print styles */
        @media print {
            .export-header {
                break-inside: avoid;
            }
            
            .message {
                break-inside: avoid;
                margin-bottom: 1em;
            }
            
            pre {
                break-inside: avoid;
            }
        }
    </style>
</head>
<body>
    <div class="export-header">
        <h1>IDEA Conversation</h1>
        <h2><a href="https://github.com/uhsealevelcenter/IDEA" target="_blank">Intelligent Data Exploring Assistant</a></h2>
        <p>Generated on: ${new Date().toLocaleString()}</p>
        <p>Total messages: ${messages.length}</p>
        <p>(Note: equations not displayed)</p>
    </div>
    
    <div class="chat-display">
        ${chatClone.innerHTML}
    </div>
    
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
