(function () {
    const state = {
        users: [],
        selectedUserId: null,
        currentUserId: null,
        isSuperuser: false,
    };

    function getEndpoints() {
        try { return config.getEndpoints(); } catch { return {}; }
    }

    function getAuthHeaders() {
        const token = localStorage.getItem('authToken');
        return token ? { 'Authorization': `Bearer ${token}` } : {};
    }

    function openModal(modal) {
        if (!modal) return;
        modal.style.display = 'block';
        document.body.style.overflow = 'hidden';
    }

    function closeModal(modal) {
        if (!modal) return;
        modal.style.display = 'none';
        document.body.style.overflow = '';
        resetForm();
    }

    function toggleAdminControls(isSuperuser) {
        const adminEls = document.querySelectorAll('.admin-only');
        adminEls.forEach((el) => {
            if (isSuperuser) {
                const desiredDisplay = el.dataset.adminDisplay || (el.classList.contains('nav-btn') ? 'inline-flex' : 'block');
                el.style.display = desiredDisplay;
            } else {
                el.style.display = '';
            }
        });
    }

    async function fetchCurrentUser() {
        try {
            const endpoints = getEndpoints();
            const url = endpoints.userProfile || '/api/users/me';
            const res = await fetch(url, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
            });
            if (!res.ok) return;
            const profile = await res.json();
            state.currentUserId = profile.id || null;
            state.isSuperuser = Boolean(profile.is_superuser);
            toggleAdminControls(state.isSuperuser);
        } catch (err) {
            console.error('Unable to determine user privileges:', err);
        }
    }

    async function loadUsers() {
        const listEl = document.getElementById('userList');
        if (!listEl) return;
        listEl.innerHTML = '<div class="loading">Loading users...</div>';

        try {
            const endpoints = getEndpoints();
            const url = endpoints.users || '/api/users';
            const res = await fetch(url, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
            });

            if (!res.ok) {
                let detail = 'Failed to load users.';
                try {
                    const err = await res.json();
                    if (err && err.detail) detail = err.detail;
                } catch { /* ignore */ }
                throw new Error(detail);
            }

            const data = await res.json();
            state.users = Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : [];
            renderUserList();
        } catch (err) {
            console.error('Error loading users:', err);
            renderUserListError(err.message || 'Failed to load users.');
        }
    }

    function renderUserListError(message) {
        const listEl = document.getElementById('userList');
        if (!listEl) return;
        listEl.innerHTML = '';
        const errorDiv = document.createElement('div');
        errorDiv.className = 'list-error';
        errorDiv.textContent = message;
        listEl.appendChild(errorDiv);
    }

    function renderUserList() {
        const listEl = document.getElementById('userList');
        if (!listEl) return;
        listEl.innerHTML = '';

        if (!state.users.length) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.textContent = 'No users found.';
            listEl.appendChild(empty);
            return;
        }

        state.users.forEach((user) => {
            const item = document.createElement('div');
            item.className = 'prompt-item user-item';
            if (state.selectedUserId === user.id) {
                item.classList.add('active');
            }

            const content = document.createElement('div');
            content.className = 'prompt-item-content user-item-content';

            const emailEl = document.createElement('div');
            emailEl.className = 'prompt-item-name user-item-email';
            emailEl.textContent = user.email || '';

            const metaEl = document.createElement('div');
            metaEl.className = 'prompt-item-meta user-item-meta';
            const pieces = [];
            pieces.push(user.full_name ? user.full_name : 'No name');
            if (user.created_at) {
                pieces.push(formatDate(user.created_at));
            }
            metaEl.textContent = pieces.join(' • ');

            const tagsEl = document.createElement('div');
            tagsEl.className = 'user-item-tags';
            if (user.is_superuser) {
                tagsEl.appendChild(createBadge('Superuser', 'badge-superuser'));
            }
            tagsEl.appendChild(createBadge(user.is_active ? 'Active' : 'Inactive', user.is_active ? 'badge-active' : 'badge-inactive'));

            content.appendChild(emailEl);
            content.appendChild(metaEl);
            content.appendChild(tagsEl);

            const actions = document.createElement('div');
            actions.className = 'prompt-item-actions user-item-actions';

            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'btn btn-secondary user-action';
            editBtn.textContent = 'Edit';
            editBtn.addEventListener('click', () => selectUser(user.id));

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'btn btn-danger user-action';
            deleteBtn.textContent = 'Delete';
            if (user.id === state.currentUserId) {
                deleteBtn.disabled = true;
                deleteBtn.title = 'You cannot delete your own account.';
            } else {
                deleteBtn.addEventListener('click', () => confirmDeleteUser(user.id));
            }

            actions.appendChild(editBtn);
            actions.appendChild(deleteBtn);

            item.appendChild(content);
            item.appendChild(actions);
            listEl.appendChild(item);
        });
    }

    function createBadge(text, extraClass) {
        const badge = document.createElement('span');
        badge.className = `status-badge ${extraClass || ''}`.trim();
        badge.textContent = text;
        return badge;
    }

    function formatDate(isoDate) {
        if (!isoDate) return '';
        try {
            const d = new Date(isoDate);
            if (Number.isNaN(d.getTime())) return '';
            return `Created ${d.toLocaleString()}`;
        } catch {
            return '';
        }
    }

    function selectUser(userId) {
        const user = state.users.find((u) => u.id === userId);
        if (!user) return;
        state.selectedUserId = userId;

        const formTitle = document.getElementById('userFormTitle');
        const fullNameInput = document.getElementById('userFullName');
        const emailInput = document.getElementById('userEmail');
        const passwordInput = document.getElementById('userPassword');
        const passwordHelper = document.getElementById('userPasswordHelper');
        const superuserInput = document.getElementById('userIsSuperuser');
        const activeInput = document.getElementById('userIsActive');

        if (formTitle) formTitle.textContent = 'Edit User';
        if (fullNameInput) fullNameInput.value = user.full_name || '';
        if (emailInput) emailInput.value = user.email || '';
        if (passwordInput) {
            passwordInput.value = '';
            passwordInput.required = false;
            passwordInput.placeholder = 'Leave blank to keep current password';
        }
        if (passwordHelper) passwordHelper.textContent = 'Leave blank to keep the current password.';
        if (superuserInput) superuserInput.checked = Boolean(user.is_superuser);
        if (activeInput) activeInput.checked = Boolean(user.is_active);

        clearMessage();
        renderUserList();
    }

    function resetForm() {
        state.selectedUserId = null;
        const form = document.getElementById('userForm');
        if (form) form.reset();

        const formTitle = document.getElementById('userFormTitle');
        if (formTitle) formTitle.textContent = 'Create User';

        const passwordInput = document.getElementById('userPassword');
        if (passwordInput) {
            passwordInput.required = true;
            passwordInput.placeholder = '';
        }
        const passwordHelper = document.getElementById('userPasswordHelper');
        if (passwordHelper) passwordHelper.textContent = 'Password must be 8-40 characters.';

        const activeInput = document.getElementById('userIsActive');
        if (activeInput) activeInput.checked = true;

        clearMessage();
        renderUserList();
    }

    function clearMessage() {
        const messageEl = document.getElementById('userFormMessage');
        if (messageEl) {
            messageEl.textContent = '';
            messageEl.className = 'form-message';
        }
    }

    function setMessage(text, type) {
        const messageEl = document.getElementById('userFormMessage');
        if (!messageEl) return;
        messageEl.textContent = text;
        messageEl.className = `form-message ${type}`;
    }

    function getFormValues() {
        const fullName = document.getElementById('userFullName')?.value?.trim() || null;
        const email = document.getElementById('userEmail')?.value?.trim() || '';
        const password = document.getElementById('userPassword')?.value || '';
        const isSuperuser = document.getElementById('userIsSuperuser')?.checked || false;
        const isActive = document.getElementById('userIsActive')?.checked ?? true;
        return { fullName, email, password, isSuperuser, isActive };
    }

    async function handleFormSubmit(event) {
        event.preventDefault();
        clearMessage();

        const { fullName, email, password, isSuperuser, isActive } = getFormValues();
        if (!email) {
            setMessage('Email is required.', 'error');
            return;
        }

        const isCreate = !state.selectedUserId;
        if (isCreate && (!password || password.length < 8 || password.length > 40)) {
            setMessage('Password must be 8-40 characters for new users.', 'error');
            return;
        }

        if (password && (password.length < 8 || password.length > 40)) {
            setMessage('Password must be 8-40 characters.', 'error');
            return;
        }

        const payload = {
            email,
            is_superuser: isSuperuser,
            is_active: isActive,
        };
        if (fullName !== null) payload.full_name = fullName;
        if (isCreate || password) payload.password = password;

        const submitBtn = document.querySelector('#userForm button[type="submit"]');
        const cancelBtn = document.getElementById('cancelUserEditBtn');
        if (submitBtn) submitBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;

        try {
            const endpoints = getEndpoints();
            const baseUrl = endpoints.users || '/api/users';
            const url = isCreate ? baseUrl : `${baseUrl}/${state.selectedUserId}`;
            const method = isCreate ? 'POST' : 'PUT';

            const res = await fetch(url, {
                method,
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                let detail = isCreate ? 'Failed to create user.' : 'Failed to update user.';
                try {
                    const err = await res.json();
                    if (err && err.detail) detail = err.detail;
                } catch { /* ignore */ }
                throw new Error(detail);
            }

            const responseData = await res.json();
            const successMessage = isCreate ? 'User created successfully.' : 'User updated successfully.';
            await loadUsers();
            if (isCreate) {
                resetForm();
            } else {
                state.selectedUserId = responseData?.id || state.selectedUserId;
                selectUser(state.selectedUserId);
            }
            setMessage(successMessage, 'success');
        } catch (err) {
            setMessage(err.message || 'Failed to save user.', 'error');
        } finally {
            if (submitBtn) submitBtn.disabled = false;
            if (cancelBtn) cancelBtn.disabled = false;
        }
    }

    async function confirmDeleteUser(userId) {
        const confirmation = window.confirm('Are you sure you want to delete this user?');
        if (!confirmation) return;
        try {
            const endpoints = getEndpoints();
            const url = `${endpoints.users || '/api/users'}/${userId}`;
            const res = await fetch(url, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeaders(),
                },
            });

            if (!res.ok) {
                let detail = 'Failed to delete user.';
                try {
                    const err = await res.json();
                    if (err && err.detail) detail = err.detail;
                } catch { /* ignore */ }
                throw new Error(detail);
            }

            if (state.selectedUserId === userId) {
                resetForm();
            }
            await loadUsers();
            setMessage('User deleted successfully.', 'success');
        } catch (err) {
            setMessage(err.message || 'Failed to delete user.', 'error');
        }
    }

    function attachEvents() {
        const modal = document.getElementById('userManagementModal');
        const openBtn = document.getElementById('userManagementButton');
        const openBtnMobile = document.getElementById('userManagementButtonMobile');
        const closeBtn = document.getElementById('closeUserManagementModal');
        const cancelBtn = document.getElementById('cancelUserEditBtn');
        const createBtn = document.getElementById('createUserButton');
        const form = document.getElementById('userForm');

        if (openBtn) openBtn.addEventListener('click', () => {
            if (!state.isSuperuser) return;
            resetForm();
            openModal(modal);
            loadUsers();
        });

        if (openBtnMobile) openBtnMobile.addEventListener('click', () => {
            if (!state.isSuperuser) return;
            resetForm();
            openModal(modal);
            loadUsers();
            const navbarMobileMenu = document.getElementById('navbarMobileMenu');
            const navbarToggle = document.getElementById('navbarToggle');
            const mobileOverlay = document.getElementById('mobileOverlay');
            if (navbarMobileMenu) navbarMobileMenu.classList.remove('active');
            if (navbarToggle) navbarToggle.classList.remove('active');
            if (mobileOverlay) mobileOverlay.classList.remove('active');
            document.body.style.overflow = '';
        });

        if (closeBtn) closeBtn.addEventListener('click', () => closeModal(modal));
        if (cancelBtn) cancelBtn.addEventListener('click', resetForm);
        if (createBtn) createBtn.addEventListener('click', resetForm);
        if (form) form.addEventListener('submit', handleFormSubmit);

        window.addEventListener('click', (e) => {
            if (e.target === modal) {
                closeModal(modal);
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal && modal.style.display === 'block') {
                closeModal(modal);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        attachEvents();
        fetchCurrentUser();
    });
})();
