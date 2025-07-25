// prompt-manager.js - System Prompt Management Interface

class PromptManager {
    constructor() {
        this.modal = null;
        this.currentPromptId = null;
        this.prompts = [];
        this.isEditing = false;
        
        this.initializeElements();
        this.attachEventListeners();
    }

    initializeElements() {
        this.modal = document.getElementById('promptManagementModal');
        this.promptsList = document.getElementById('promptsList');
        this.promptEditor = document.getElementById('promptEditor');
        this.promptPreview = document.getElementById('promptPreview');
        this.promptForm = document.getElementById('promptForm');
        
        // Form elements
        this.promptName = document.getElementById('promptName');
        this.promptDescription = document.getElementById('promptDescription');
        this.promptContent = document.getElementById('promptContent');
        this.editorTitle = document.getElementById('editorTitle');
    }

    attachEventListeners() {
        // Modal open/close
        const systemPromptBtn = document.getElementById('systemPromptButton');
        const systemPromptBtnMobile = document.getElementById('systemPromptButtonMobile');
        const closeModalBtn = document.getElementById('closePromptModal');
        
        if (systemPromptBtn) {
            systemPromptBtn.addEventListener('click', () => this.openModal());
        }
        if (systemPromptBtnMobile) {
            systemPromptBtnMobile.addEventListener('click', () => {
                this.openModal();
                // Close mobile menu if open
                this.closeMobileMenu();
            });
        }
        if (closeModalBtn) {
            closeModalBtn.addEventListener('click', () => this.closeModal());
        }

        // Close modal on outside click
        window.addEventListener('click', (event) => {
            if (event.target === this.modal) {
                this.closeModal();
            }
        });

        // Create new prompt button
        const createNewBtn = document.getElementById('createNewPromptBtn');
        if (createNewBtn) {
            createNewBtn.addEventListener('click', () => this.createNewPrompt());
        }

        // Form submission
        if (this.promptForm) {
            this.promptForm.addEventListener('submit', (e) => this.handleFormSubmit(e));
        }

        // Cancel edit button
        const cancelEditBtn = document.getElementById('cancelEditBtn');
        if (cancelEditBtn) {
            cancelEditBtn.addEventListener('click', () => this.cancelEdit());
        }

        // Escape key to close modal
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modal.style.display === 'block') {
                this.closeModal();
            }
        });
    }

    closeMobileMenu() {
        const navbarToggle = document.getElementById('navbarToggle');
        const navbarMobileMenu = document.getElementById('navbarMobileMenu');
        const mobileOverlay = document.getElementById('mobileOverlay');
        
        if (navbarToggle) navbarToggle.classList.remove('active');
        if (navbarMobileMenu) navbarMobileMenu.classList.remove('active');
        if (mobileOverlay) mobileOverlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    async openModal() {
        this.modal.style.display = 'block';
        document.body.style.overflow = 'hidden';
        await this.loadPrompts();
    }

    closeModal() {
        this.modal.style.display = 'none';
        document.body.style.overflow = '';
        this.resetEditor();
        // Clear current selection so it auto-selects when reopened
        this.currentPromptId = null;
    }

    async loadPrompts() {
        try {
            const response = await fetch(config.getEndpoints().prompts, {
                method: 'GET',
                headers: {
                    ...getAuthHeaders()
                }
            });

            if (!response.ok) {
                throw new Error('Failed to load prompts');
            }

            this.prompts = await response.json();
            this.renderPromptsList();
        } catch (error) {
            console.error('Error loading prompts:', error);
            this.showError('Failed to load prompts. Please try again.');
        }
    }

    renderPromptsList() {
        if (!this.promptsList) return;

        this.promptsList.innerHTML = '';

        if (this.prompts.length === 0) {
            this.promptsList.innerHTML = '<div class="no-prompts">No prompts found</div>';
            return;
        }

        this.prompts.forEach(prompt => {
            const promptItem = this.createPromptListItem(prompt);
            this.promptsList.appendChild(promptItem);
        });

        // Auto-select the active prompt if no current selection, or the first prompt if no active one
        if (!this.currentPromptId && this.prompts.length > 0) {
            const activePrompt = this.prompts.find(p => p.is_active);
            const promptToSelect = activePrompt || this.prompts[0];
            // Add small delay to ensure DOM is fully ready
            setTimeout(() => {
                this.selectPrompt(promptToSelect.id);
            }, 100);
        }
    }

    createPromptListItem(prompt) {
        const item = document.createElement('div');
        item.className = `prompt-item ${prompt.is_active ? 'active' : ''}`;
        item.dataset.promptId = prompt.id;

        const formattedDate = new Date(prompt.updated_at).toLocaleDateString();

        item.innerHTML = `
            <div class="prompt-item-content">
                <div class="prompt-item-name">
                    ${this.escapeHtml(prompt.name)}
                    ${prompt.is_active ? '<span class="active-badge">Active</span>' : ''}
                </div>
                <div class="prompt-item-description">${this.escapeHtml(prompt.description || 'No description')}</div>
                <div class="prompt-item-meta">Updated: ${formattedDate}</div>
            </div>
            <div class="prompt-item-actions">
                <button class="btn btn-sm btn-primary edit-prompt" title="Edit">
                    <span class="material-icons">edit</span>
                </button>
                ${!prompt.is_active ? `
                    <button class="btn btn-sm btn-success activate-prompt" title="Set as Active">
                        <span class="material-icons">check_circle</span>
                    </button>
                ` : ''}
                <button class="btn btn-sm btn-danger delete-prompt" title="Delete">
                    <span class="material-icons">delete</span>
                </button>
            </div>
        `;

        // Add event listeners
        item.addEventListener('click', (e) => {
            if (!e.target.closest('.prompt-item-actions')) {
                this.selectPrompt(prompt.id);
            }
        });

        const editBtn = item.querySelector('.edit-prompt');
        const activateBtn = item.querySelector('.activate-prompt');
        const deleteBtn = item.querySelector('.delete-prompt');

        if (editBtn) {
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.editPrompt(prompt.id);
            });
        }

        if (activateBtn) {
            activateBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.setActivePrompt(prompt.id);
            });
        }

        if (deleteBtn) {
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deletePrompt(prompt.id);
            });
        }

        return item;
    }

    selectPrompt(promptId) {
        // Update visual selection
        this.promptsList.querySelectorAll('.prompt-item').forEach(item => {
            item.classList.remove('selected');
        });

        const selectedItem = this.promptsList.querySelector(`[data-prompt-id="${promptId}"]`);
        if (selectedItem) {
            selectedItem.classList.add('selected');
        }

        this.currentPromptId = promptId;
        // Go directly to edit view instead of preview
        this.editPrompt(promptId);
    }

    async showPromptPreview(promptId) {
        const prompt = this.prompts.find(p => p.id === promptId);
        if (!prompt) return;

        this.promptPreview.innerHTML = `
            <h3>${this.escapeHtml(prompt.name)} ${prompt.is_active ? '<span class="active-badge">Active</span>' : ''}</h3>
            <p class="prompt-description">${this.escapeHtml(prompt.description || 'No description')}</p>
            <div class="prompt-meta">
                <p><strong>Created:</strong> ${new Date(prompt.created_at).toLocaleString()}</p>
                <p><strong>Updated:</strong> ${new Date(prompt.updated_at).toLocaleString()}</p>
            </div>
            <div class="prompt-content-preview">
                <h4>Content Preview:</h4>
                <pre>${this.escapeHtml(prompt.content.substring(0, 500))}${prompt.content.length > 500 ? '...' : ''}</pre>
            </div>
            <div class="preview-actions">
                <button class="btn btn-primary" onclick="promptManager.editPrompt('${prompt.id}')">
                    <span class="material-icons">edit</span>
                    Edit Prompt
                </button>
                ${!prompt.is_active ? `
                    <button class="btn btn-success" onclick="promptManager.setActivePrompt('${prompt.id}')">
                        <span class="material-icons">check_circle</span>
                        Set as Active
                    </button>
                ` : ''}
            </div>
        `;

        this.promptPreview.style.display = 'block';
        this.promptEditor.style.display = 'none';
    }

    createNewPrompt() {
        this.currentPromptId = null;
        this.isEditing = true;
        this.editorTitle.textContent = 'Create New Prompt';
        
        // Clear form
        this.promptName.value = '';
        this.promptDescription.value = '';
        this.promptContent.value = '';
        
        this.promptPreview.style.display = 'none';
        this.promptEditor.style.display = 'block';
        
        this.promptName.focus();
    }

    editPrompt(promptId) {
        const prompt = this.prompts.find(p => p.id === promptId);
        if (!prompt) {
            console.error(`Prompt with ID ${promptId} not found in prompts list:`, this.prompts);
            return;
        }

        this.currentPromptId = promptId;
        this.isEditing = true;
        this.editorTitle.textContent = `Edit Prompt: ${prompt.name}`;
        
        // Populate form with safety checks
        if (this.promptName) this.promptName.value = prompt.name || '';
        if (this.promptDescription) this.promptDescription.value = prompt.description || '';
        if (this.promptContent) this.promptContent.value = prompt.content || '';
        
        this.promptPreview.style.display = 'none';
        this.promptEditor.style.display = 'block';
        
        if (this.promptName) this.promptName.focus();
    }

    async handleFormSubmit(e) {
        e.preventDefault();
        
        const formData = {
            name: this.promptName.value.trim(),
            description: this.promptDescription.value.trim(),
            content: this.promptContent.value.trim()
        };

        if (!formData.name || !formData.content) {
            this.showError('Name and content are required.');
            return;
        }

        try {
            let response;
            
            if (this.currentPromptId) {
                // Update existing prompt
                response = await fetch(`${config.getEndpoints().prompts}/${this.currentPromptId}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        ...getAuthHeaders()
                    },
                    body: JSON.stringify(formData)
                });
            } else {
                // Create new prompt
                response = await fetch(config.getEndpoints().prompts, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...getAuthHeaders()
                    },
                    body: JSON.stringify(formData)
                });
            }

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to save prompt');
            }

            const savedPrompt = await response.json();
            
            // Refresh the prompts list
            await this.loadPrompts();
            
            // Show success message
            this.showSuccess(`Prompt "${savedPrompt.name}" ${this.currentPromptId ? 'updated' : 'created'} successfully!`);
            
            // Update current prompt and continue editing with the saved data
            this.currentPromptId = savedPrompt.id;
            this.isEditing = true;
            this.editorTitle.textContent = `Edit Prompt: ${savedPrompt.name}`;
            
            // Populate form directly with saved data
            this.promptName.value = savedPrompt.name || '';
            this.promptDescription.value = savedPrompt.description || '';
            this.promptContent.value = savedPrompt.content || '';
            
            this.promptPreview.style.display = 'none';
            this.promptEditor.style.display = 'block';

        } catch (error) {
            console.error('Error saving prompt:', error);
            this.showError(error.message || 'Failed to save prompt. Please try again.');
        }
    }

    async setActivePrompt(promptId) {
        try {
            const response = await fetch(config.getEndpoints().setActivePrompt, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders()
                },
                body: JSON.stringify({ prompt_id: promptId })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to set active prompt');
            }

            // Refresh the prompts list
            await this.loadPrompts();
            
            const prompt = this.prompts.find(p => p.id === promptId);
            this.showSuccess(`"${prompt?.name}" is now the active system prompt.`);
            
            // Update the preview if this prompt is selected
            if (this.currentPromptId === promptId) {
                this.showPromptPreview(promptId);
            }

        } catch (error) {
            console.error('Error setting active prompt:', error);
            this.showError(error.message || 'Failed to set active prompt. Please try again.');
        }
    }

    async deletePrompt(promptId) {
        const prompt = this.prompts.find(p => p.id === promptId);
        if (!prompt) return;

        if (!confirm(`Are you sure you want to delete "${prompt.name}"?`)) {
            return;
        }

        try {
            const response = await fetch(`${config.getEndpoints().prompts}/${promptId}`, {
                method: 'DELETE',
                headers: {
                    ...getAuthHeaders()
                }
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to delete prompt');
            }

            // Refresh the prompts list
            await this.loadPrompts();
            
            this.showSuccess(`Prompt "${prompt.name}" deleted successfully.`);
            
            // Reset editor if this prompt was being edited
            if (this.currentPromptId === promptId) {
                this.resetEditor();
            }

        } catch (error) {
            console.error('Error deleting prompt:', error);
            this.showError(error.message || 'Failed to delete prompt. Please try again.');
        }
    }

    cancelEdit() {
        this.resetEditor();
        if (this.currentPromptId) {
            this.showPromptPreview(this.currentPromptId);
        }
    }

    resetEditor() {
        this.isEditing = false;
        this.promptEditor.style.display = 'none';
        
        if (this.currentPromptId) {
            this.promptPreview.style.display = 'block';
        } else {
            this.promptPreview.innerHTML = `
                <h3>Select a prompt to view or edit</h3>
                <p>Choose a prompt from the list on the left to view its content or make changes.</p>
            `;
            this.promptPreview.style.display = 'block';
        }
        
        // Clear form
        this.promptForm.reset();
        this.currentPromptId = null;
    }

    showSuccess(message) {
        this.showToast(message, 'success');
    }

    showError(message) {
        this.showToast(message, 'error');
    }

    showToast(message, type = 'info') {
        // Create toast notification
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        
        // Style the toast
        Object.assign(toast.style, {
            position: 'fixed',
            top: '20px',
            right: '20px',
            padding: '12px 24px',
            borderRadius: '6px',
            color: 'white',
            fontSize: '14px',
            fontWeight: '500',
            zIndex: '3000',
            maxWidth: '400px',
            opacity: '0',
            transform: 'translateX(100%)',
            transition: 'all 0.3s ease',
            background: type === 'success' ? '#28a745' : 
                       type === 'error' ? '#dc3545' : '#17a2b8'
        });
        
        document.body.appendChild(toast);
        
        // Animate in
        setTimeout(() => {
            toast.style.opacity = '1';
            toast.style.transform = 'translateX(0)';
        }, 100);
        
        // Auto remove
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100%)';
            setTimeout(() => {
                if (toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
            }, 300);
        }, 3000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize prompt manager when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // Make sure we have the getAuthHeaders function available
    if (typeof getAuthHeaders === 'function') {
        window.promptManager = new PromptManager();
    } else {
        console.warn('getAuthHeaders function not available. Prompt manager will initialize after authentication setup.');
        // Retry initialization after a short delay
        setTimeout(() => {
            if (typeof getAuthHeaders === 'function') {
                window.promptManager = new PromptManager();
            }
        }, 1000);
    }
});

// Add some additional CSS for the toast notifications and preview
const additionalCSS = `
    .prompt-description {
        font-style: italic;
        color: #666;
        margin-bottom: 1rem;
    }
    
    .prompt-meta {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 6px;
        margin-bottom: 1rem;
    }
    
    .prompt-meta p {
        margin-bottom: 0.5rem;
    }
    
    .prompt-content-preview {
        margin-bottom: 1.5rem;
    }
    
    .prompt-content-preview h4 {
        margin-bottom: 0.5rem;
        color: #333;
    }
    
    .prompt-content-preview pre {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 6px;
        border: 1px solid #e9ecef;
        font-size: 0.85rem;
        line-height: 1.4;
        overflow-x: auto;
    }
    
    .preview-actions {
        display: flex;
        gap: 1rem;
        flex-wrap: wrap;
    }
    
    .btn-sm {
        padding: 0.25rem 0.5rem;
        font-size: 0.8rem;
    }
    
    .prompt-item.selected {
        background-color: #e8f4fd !important;
        border-left: 4px solid #007bff;
    }
    
    .no-prompts {
        text-align: center;
        padding: 2rem;
        color: #666;
        font-style: italic;
    }
`;

// Inject additional CSS
const style = document.createElement('style');
style.textContent = additionalCSS;
document.head.appendChild(style); 