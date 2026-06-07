/* ClassFlow — API Client */
'use strict';

/* ════════════════════════════════════════════
   API
════════════════════════════════════════════ */
async function api(path, opts = {}) {
    const url = path.startsWith('http') ? path : `${API_BASE_URL}${path}`;
    
    // Copy default headers and inject active session bearer token
    const headers = { ...HEADERS };
    const token = localStorage.getItem('cf_auth_token');
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const r = await fetch(url, { headers, ...opts });
    if (!r.ok) {
        if (r.status === 401 && !path.startsWith('/auth/')) {
            if (typeof logout === 'function') logout();
        }
        const e = await r.json().catch(() => ({}));
        throw new Error(e.error || `HTTP ${r.status}`);
    }
    return r.json();
}
