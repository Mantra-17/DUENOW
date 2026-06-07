/* ClassFlow — API Client */
'use strict';

/* ════════════════════════════════════════════
   API
════════════════════════════════════════════ */
async function api(path, opts = {}) {
    const url = path.startsWith('http') ? path : `${API_BASE_URL}${path}`;
    const r = await fetch(url, { headers: HEADERS, ...opts });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || `HTTP ${r.status}`); }
    return r.json();
}
