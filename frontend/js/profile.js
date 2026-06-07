'use strict';
/* ClassFlow — Profile Dropdown */

/* ════════════════════════════════════════════
   PROFILE DROPDOWN
════════════════════════════════════════════ */
function toggleProfile() {
    const drop = id('profile-drop');
    const isOpen = drop.classList.contains('open');
    if (isOpen) closeProfile();
    else openProfile();
}

function openProfile() {
    // Update stats in dropdown
    const total   = S.tasks.length;
    const pending = S.tasks.filter(t => !t.is_completed).length;
    const done    = S.tasks.filter(t =>  t.is_completed).length;
    setText('pd-total',   total);
    setText('pd-pending', pending);
    setText('pd-done',    done);

    // Update theme label
    const theme = document.body.dataset.theme;
    setText('pd-theme-lbl', theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode');

    // Update Push subscription toggle checkbox state
    if (window.checkPushSubscriptionState) {
        window.checkPushSubscriptionState();
    }

    id('profile-drop').classList.add('open');
    // Close on outside click
    setTimeout(() => document.addEventListener('click', outsideProfileClick), 0);
}

function closeProfile() {
    id('profile-drop').classList.remove('open');
    document.removeEventListener('click', outsideProfileClick);
}

function outsideProfileClick(e) {
    if (!id('profile-wrap').contains(e.target)) closeProfile();
}

function toggleThemeFromMenu() {
    const cur = document.body.dataset.theme;
    setTheme(cur === 'dark' ? 'light' : 'dark');
    setText('pd-theme-lbl', cur === 'dark' ? 'Switch to Dark Mode' : 'Switch to Light Mode');
}
