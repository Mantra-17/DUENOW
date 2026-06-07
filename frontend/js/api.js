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

/* ════════════════════════════════════════════
   ERROR BANNER
════════════════════════════════════════════ */
function showErr(msg) {
    const b = id('err-banner');
    b.style.display = 'flex';
    id('err-msg').textContent = 'API error: ' + msg;
}

function hideErr() { id('err-banner').style.display = 'none'; }
