class KnowledgeBaseManager {
    constructor() {
        this.modal = null;
        this.uploadArea = null;
        this.fileInput = null;
        this.papersList = null;
        this.uploadProgress = null;
        this.endpoints = config.getEndpoints();
        this.init();
    }

    init() {
        this.setupElements();
        this.bindEvents();
    }

    setupElements() {
        this.modal = document.getElementById('knowledgeBaseModal');
        this.uploadArea = document.getElementById('knowledgeBaseUploadArea');
        this.fileInput = document.getElementById('knowledgeBaseFileInput');
        this.papersList = document.getElementById('papersList');
        this.uploadProgress = document.getElementById('knowledgeBaseUploadProgress');
    }

    bindEvents() {
        // Desktop and mobile button events
        const knowledgeBaseButton = document.getElementById('knowledgeBaseButton');
        const knowledgeBaseButtonMobile = document.getElementById('knowledgeBaseButtonMobile');
        const closeButton = document.getElementById('closeKnowledgeBaseModal');
        const browseButton = document.getElementById('browseKnowledgeBaseFiles');

        if (knowledgeBaseButton) {
            knowledgeBaseButton.addEventListener('click', () => this.openModal());
        }
        if (knowledgeBaseButtonMobile) {
            knowledgeBaseButtonMobile.addEventListener('click', () => {
                this.openModal();
                // Close mobile menu
                const mobileMenu = document.getElementById('navbarMobileMenu');
                const overlay = document.getElementById('mobileOverlay');
                if (mobileMenu) mobileMenu.classList.remove('show');
                if (overlay) overlay.classList.remove('active');
            });
        }
        if (closeButton) {
            closeButton.addEventListener('click', () => this.closeModal());
        }
        if (browseButton) {
            browseButton.addEventListener('click', () => this.fileInput.click());
        }

        // File input change event
        if (this.fileInput) {
            this.fileInput.addEventListener('change', (e) => this.handleFileSelect(e.target.files));
        }

        // Drag and drop events
        if (this.uploadArea) {
            this.uploadArea.addEventListener('dragover', (e) => this.handleDragOver(e));
            this.uploadArea.addEventListener('dragleave', (e) => this.handleDragLeave(e));
            this.uploadArea.addEventListener('drop', (e) => this.handleDrop(e));
        }

        // Close modal when clicking outside
        window.addEventListener('click', (e) => {
            if (e.target === this.modal) {
                this.closeModal();
            }
        });
    }

    async openModal() {
        this.modal.style.display = 'block';
        document.body.classList.add('modal-open');
        await this.loadPapers();
        await this.loadStats();
    }

    closeModal() {
        this.modal.style.display = 'none';
        document.body.classList.remove('modal-open');
    }

    handleDragOver(e) {
        e.preventDefault();
        e.stopPropagation(); // Prevent the global drag handler from firing
        this.uploadArea.classList.add('drag-over');
    }

    handleDragLeave(e) {
        e.preventDefault();
        this.uploadArea.classList.remove('drag-over');
    }

    handleDrop(e) {
        e.preventDefault();
        e.stopPropagation(); // Prevent the global drop handler from firing
        this.uploadArea.classList.remove('drag-over');
        this.handleFileSelect(e.dataTransfer.files);
    }

    async handleFileSelect(files) {
        if (!files || files.length === 0) return;

        for (let file of files) {
            await this.uploadFile(file);
        }
        
        // Refresh the papers list and stats after upload
        await this.loadPapers();
        await this.loadStats();
    }

    async uploadFile(file) {
        const formData = new FormData();
        formData.append('file', file);

        try {
            this.showUploadProgress();
            
            const response = await fetch(this.endpoints.knowledgeBaseUpload || '/idea-api/knowledge-base/papers/upload', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.getAuthToken()}`
                },
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Upload failed');
            }

            const result = await response.json();
            this.showMessage(`Successfully uploaded: ${result.filename}`, 'success');
            
        } catch (error) {
            console.error('Upload error:', error);
            this.showMessage(`Failed to upload ${file.name}: ${error.message}`, 'error');
        } finally {
            this.hideUploadProgress();
        }
    }

    async loadPapers() {
        try {
            const response = await fetch(this.endpoints.knowledgeBase || '/idea-api/knowledge-base/papers', {
                headers: {
                    'Authorization': `Bearer ${this.getAuthToken()}`
                }
            });

            if (!response.ok) {
                throw new Error('Failed to load papers');
            }

            const data = await response.json();
            this.renderPapers(data.papers);
            
        } catch (error) {
            console.error('Error loading papers:', error);
            this.papersList.innerHTML = '<div class="error">Failed to load papers</div>';
        }
    }

    async loadStats() {
        try {
            const response = await fetch(this.endpoints.knowledgeBaseStats || '/idea-api/knowledge-base/stats', {
                headers: {
                    'Authorization': `Bearer ${this.getAuthToken()}`
                }
            });

            if (!response.ok) {
                throw new Error('Failed to load stats');
            }

            const data = await response.json();
            this.renderStats(data);
            
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    renderPapers(papers) {
        if (!papers || papers.length === 0) {
            this.papersList.innerHTML = '<div class="no-papers">No papers found. Upload some papers to get started.</div>';
            return;
        }

        const papersHtml = papers.map(paper => `
            <div class="paper-item" data-filename="${paper.name}">
                <div class="paper-info">
                    <div class="paper-name">
                        <span class="material-icons file-icon">${this.getFileIcon(paper.extension)}</span>
                        <span class="filename">${paper.name}</span>
                    </div>
                    <div class="paper-meta">
                        <span class="file-size">${this.formatFileSize(paper.size)}</span>
                        <span class="file-date">${this.formatDate(paper.modified)}</span>
                    </div>
                </div>
                <div class="paper-actions">
                    <button class="btn btn-danger btn-sm delete-paper" data-filename="${paper.name}">
                        <span class="material-icons">delete</span>
                        Delete
                    </button>
                </div>
            </div>
        `).join('');

        this.papersList.innerHTML = papersHtml;

        // Bind delete events
        this.papersList.querySelectorAll('.delete-paper').forEach(button => {
            button.addEventListener('click', (e) => {
                const filename = e.currentTarget.dataset.filename;
                this.deletePaper(filename);
            });
        });
    }

    renderStats(stats) {
        const totalFilesEl = document.getElementById('totalFiles');
        const totalSizeEl = document.getElementById('totalSize');

        if (totalFilesEl) {
            totalFilesEl.textContent = `${stats.total_files} files`;
        }
        if (totalSizeEl) {
            totalSizeEl.textContent = this.formatFileSize(stats.total_size);
        }
    }

    async deletePaper(filename) {
        if (!confirm(`Are you sure you want to delete "${filename}"? This action cannot be undone.`)) {
            return;
        }

        try {
            const response = await fetch(`${(this.endpoints.knowledgeBase || '/idea-api/knowledge-base/papers')}/${encodeURIComponent(filename)}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${this.getAuthToken()}`
                }
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Delete failed');
            }

            this.showMessage(`Successfully deleted: ${filename}`, 'success');
            await this.loadPapers();
            await this.loadStats();
            
        } catch (error) {
            console.error('Delete error:', error);
            this.showMessage(`Failed to delete ${filename}: ${error.message}`, 'error');
        }
    }

    getFileIcon(extension) {
        switch (extension) {
            case '.pdf':
                return 'picture_as_pdf';
            case '.doc':
            case '.docx':
                return 'description';
            case '.txt':
                return 'text_snippet';
            case '.md':
                return 'article';
            default:
                return 'insert_drive_file';
        }
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    formatDate(timestamp) {
        return new Date(timestamp * 1000).toLocaleDateString();
    }

    showUploadProgress() {
        if (this.uploadProgress) {
            this.uploadProgress.style.display = 'block';
        }
    }

    hideUploadProgress() {
        if (this.uploadProgress) {
            this.uploadProgress.style.display = 'none';
        }
    }

    showMessage(message, type = 'info') {
        // Create a simple toast notification
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        
        // Style the toast
        Object.assign(toast.style, {
            position: 'fixed',
            top: '20px',
            right: '20px',
            padding: '12px 20px',
            borderRadius: '4px',
            color: 'white',
            fontSize: '14px',
            zIndex: '10000',
            maxWidth: '400px',
            wordWrap: 'break-word'
        });

        if (type === 'success') {
            toast.style.backgroundColor = '#4CAF50';
        } else if (type === 'error') {
            toast.style.backgroundColor = '#f44336';
        } else {
            toast.style.backgroundColor = '#2196F3';
        }

        document.body.appendChild(toast);

        // Auto remove after 3 seconds
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 3000);
    }

    getAuthToken() {
        return localStorage.getItem('authToken');
    }
}

// Initialize knowledge base manager when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.knowledgeBaseManager = new KnowledgeBaseManager();
}); 