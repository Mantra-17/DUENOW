/* ClassFlow — Authentication Client & Session Manager */
'use strict';

// Global auth elements
window.logout = logout;
window.checkAuth = checkAuth;

// Parse token on startup if present in URL
(function handleUrlToken() {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');
    if (token) {
        localStorage.setItem('cf_auth_token', token);
        // Clean the URL bar without reloading
        const cleanUrl = window.location.pathname + window.location.hash;
        window.history.replaceState({}, document.title, cleanUrl);
    }
})();

async function checkAuth() {
    const token = localStorage.getItem('cf_auth_token');
    
    if (!token) {
        showLoginScreen();
        return false;
    }

    try {
        // Fetch current user info
        const data = await api('/auth/me');
        if (data && data.user) {
            setupUser(data.user);
            return true;
        } else {
            logout();
            return false;
        }
    } catch (e) {
        console.error('Failed to authenticate user profile:', e);
        // If unauthorized or expired, force logout. Otherwise it might be a transient network issue.
        if (e.message.includes('401') || e.message.includes('Unauthorized') || e.message.includes('expired')) {
            logout();
        } else {
            // Network error fallback: allow offline view if tasks are in local state
            // but keep UI logged in.
            const cachedUser = localStorage.getItem('cf_cached_user');
            if (cachedUser) {
                try {
                    setupUser(JSON.parse(cachedUser));
                    return true;
                } catch(e) {}
            }
            showLoginScreen();
        }
        return false;
    }
}

function showLoginScreen() {
    // Hide main application shell and standalone floating buttons
    const shell = document.querySelector('.shell');
    if (shell) shell.style.display = 'none';
    
    const fab = id('fab');
    if (fab) fab.style.display = 'none';
    
    const mobNav = document.querySelector('.mob-nav');
    if (mobNav) mobNav.style.display = 'none';
    
    // Show login screen
    let loginScreen = id('login-screen');
    if (!loginScreen) {
        createLoginScreenElement();
        loginScreen = id('login-screen');
    }
    if (loginScreen) {
        loginScreen.style.display = 'flex';
    }
}

function setupUser(user) {
    // Save to cache for offline loading
    localStorage.setItem('cf_cached_user', JSON.stringify(user));
    
    // Set user profile info in top bar & dropdown menu
    const nameEls = document.querySelectorAll('.pd-name');
    nameEls.forEach(el => el.textContent = user.name);
    
    const emailEls = document.querySelectorAll('.pd-role');
    emailEls.forEach(el => el.textContent = user.email);

    const smImg = id('avatar-img-sm');
    const lgImg = id('avatar-img-lg');
    const smBtn = id('avatar-btn');
    const lgBtn = document.querySelector('.pd-avatar-lg');
    const initial = (user.name ? user.name.charAt(0) : 'S').toUpperCase();

    // Clear any previous fallback text nodes
    if (smBtn) {
        Array.from(smBtn.childNodes).forEach(node => {
            if (node.nodeType === Node.TEXT_NODE) node.remove();
        });
    }
    if (lgBtn) {
        Array.from(lgBtn.childNodes).forEach(node => {
            if (node.nodeType === Node.TEXT_NODE) node.remove();
        });
    }

    if (smImg) smImg.style.display = 'block';
    if (lgImg) lgImg.style.display = 'block';

    // Setup consistent DiceBear seed or Google picture
    if (user.picture) {
        if (smImg) {
            smImg.src = user.picture;
            smImg.onerror = () => {
                smImg.style.display = 'none';
                if (smBtn) smBtn.appendChild(document.createTextNode(initial));
            };
        }
        if (lgImg) {
            lgImg.src = user.picture;
            lgImg.onerror = () => {
                lgImg.style.display = 'none';
                if (lgBtn) lgBtn.appendChild(document.createTextNode(initial));
            };
        }
    } else {
        // Set email/id as seed to get a stable, device-independent avatar
        localStorage.setItem('cf-avatar-seed', user.email || user.id);
        if (window.initAvatar) {
            window.initAvatar();
        }
    }

    // Hide login screen
    const loginScreen = id('login-screen');
    if (loginScreen) {
        loginScreen.style.display = 'none';
    }
    
    // Show application components
    const shell = document.querySelector('.shell');
    if (shell) shell.style.display = 'grid'; // Base CSS uses grid layout
    
    const fab = id('fab');
    if (fab) fab.style.display = 'flex';
    
    const mobNav = document.querySelector('.mob-nav');
    if (mobNav) mobNav.style.display = 'flex';
}

function logout() {
    localStorage.removeItem('cf_auth_token');
    localStorage.removeItem('cf_cached_user');
    
    // Reset state tasks
    if (typeof S !== 'undefined') {
        S.tasks = [];
        S.subjects = [];
        S.stats = null;
    }
    
    showLoginScreen();
}

function createLoginScreenElement() {
    const loginDiv = document.createElement('div');
    loginDiv.id = 'login-screen';
    loginDiv.className = 'login-container';
    
    // Dynamically fetch auth endpoint URL
    const loginUrl = `${API_BASE_URL}/auth/login`;
    
    loginDiv.innerHTML = `
        <div class="login-bg-glow">
            <div class="glow-orb glow-1"></div>
            <div class="glow-orb glow-2"></div>
        </div>
        <div class="login-card">
            <div class="login-logo">
                <svg viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="2"  y="3"  width="18" height="2.5" rx="1.2" fill="rgba(255,255,255,.95)"/>
                    <rect x="2"  y="9.7" width="13" height="2.5" rx="1.2" fill="rgba(255,255,255,.75)"/>
                    <rect x="2"  y="16.4" width="16" height="2.5" rx="1.2" fill="rgba(255,255,255,.95)"/>
                    <circle cx="18" cy="17" r="4" fill="#34A853"/>
                    <path d="M16 17l1.4 1.5L20 15" stroke="white" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </div>
            <h1 class="login-title">ClassFlow</h1>
            <p class="login-subtitle">AI-Powered Google Classroom Dashboard</p>
            
            <div class="login-features">
                <div class="feature-item">
                    <span class="material-icons-round">sync</span>
                    <div>
                        <h3>Automatic Google Classroom Sync</h3>
                        <p>Sync all your assignments and deadlines instantly.</p>
                    </div>
                </div>
                <div class="feature-item">
                    <span class="material-icons-round">auto_awesome</span>
                    <div>
                        <h3>Gemini AI Analysis & Insights</h3>
                        <p>Get automatic task summaries, estimated time, and difficulty.</p>
                    </div>
                </div>
                <div class="feature-item">
                    <span class="material-icons-round">notifications_active</span>
                    <div>
                        <h3>Smart Deadline Reminders</h3>
                        <p>Never miss a practical, exam, or project submission again.</p>
                    </div>
                </div>
            </div>

            <a href="${loginUrl}" class="login-btn">
                <svg viewBox="0 0 48 48" width="22" height="22" xmlns="http://www.w3.org/2000/svg">
                    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
                    <path fill="#4285F4" d="M46.5 24c0-1.61-.15-3.16-.42-4.67H24v8.86h12.62c-.54 2.89-2.18 5.34-4.63 6.98l7.19 5.57c4.21-3.87 6.62-9.57 6.62-16.74z"/>
                    <path fill="#FBBC05" d="M10.54 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.98-6.19z"/>
                    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.19-5.57c-1.99 1.33-4.51 2.13-7.2 2.13-6.26 0-11.57-4.22-13.46-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
                </svg>
                Sign In with Google
            </a>
            
            <p class="login-footer">Secure Google OAuth verification. No credentials stored.</p>
        </div>
    `;
    
    document.body.appendChild(loginDiv);
}
