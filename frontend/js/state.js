/* ClassFlow — Global State & Utility Helpers */
'use strict';

/* ════════════════════════════════════════════
   CONFIG
════════════════════════════════════════════ */
const API_KEY      = 'Classflow123';
const HEADERS      = { 'X-API-KEY': API_KEY, 'Content-Type': 'application/json' };
const REFRESH_MS   = 60_000;

// Determine API base URL dynamically for Capacitor native WebViews
const API_BASE_URL = (function() {
    if (typeof CONFIG !== 'undefined' && CONFIG.API_URL) {
        return CONFIG.API_URL;
    }
    const isCapacitor = window.location.hostname === 'localhost' && window.location.port === '';
    if (isCapacitor) {
        // Fallback to Android Emulator local loopback. For physical devices, replace with host PC's local IP (e.g. http://192.168.x.x:5001)
        return 'http://10.0.2.2:5001';
    }
    return '';
})();

/* ════════════════════════════════════════════
   STATE
════════════════════════════════════════════ */
let S = {
    tasks:    [],
    subjects: [],
    stats:    null,
    view:     'dashboard',
    filter:   'all',
    search:   '',
};

/* ════════════════════════════════════════════
   TINY UTILITY HELPERS
════════════════════════════════════════════ */
function id(i) { return document.getElementById(i); }
function setText(i, v) { const e = id(i); if(e) e.textContent = v; }

function esc(s) {
    if (!s) return '';
    return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function shortSub(s) {
    const m = s.match(/^([A-Z]{2,6}\d{3}[A-Z]?)/);
    if (m) return m[1];
    return s.length > 22 ? s.slice(0,20)+'…' : s;
}

function empty(ico, title, desc) {
    return `<div class="empty"><span class="material-icons-round">${ico}</span><h3>${title}</h3><p>${desc}</p></div>`;
}

function getCleanInitial(name, email) {
    let cleanName = (name || '').trim();
    // Strip roll numbers like 24CS030 or 24dcs076 from the beginning
    cleanName = cleanName.replace(/^\d+[a-zA-Z]+\d+\s*/, '');
    
    // Find the first letter in the cleaned name
    let match = cleanName.match(/[a-zA-Z]/);
    if (match) {
        return match[0].toUpperCase();
    }
    
    // If not found, try the original name
    if (name) {
        match = name.match(/[a-zA-Z]/);
        if (match) return match[0].toUpperCase();
    }
    
    // Try the email
    if (email) {
        let cleanEmail = email.replace(/^\d+[a-zA-Z]+\d+/, '');
        match = cleanEmail.match(/[a-zA-Z]/);
        if (match) return match[0].toUpperCase();
    }
    
    return 'S';
}

