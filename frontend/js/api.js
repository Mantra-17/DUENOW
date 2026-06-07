/* ClassFlow — API Client */
'use strict';

/* ════════════════════════════════════════════
   API
════════════════════════════════════════════ */
async function api(path, opts = {}) {
    const r = await fetch(path, { headers: HEADERS, ...opts });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || `HTTP ${r.status}`); }
    return r.json();
}
