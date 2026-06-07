/* ClassFlow — Light/Dark Theme */
'use strict';

/* ════════════════════════════════════════════
   THEME
════════════════════════════════════════════ */
function setTheme(t) {
    document.body.dataset.theme = t;
    localStorage.setItem('cf-theme', t);
    id('tbtn-light').classList.toggle('on', t === 'light');
    id('tbtn-dark').classList.toggle('on',  t === 'dark');
}

function initTheme() {
    const saved = localStorage.getItem('cf-theme')
        || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    setTheme(saved);
}
