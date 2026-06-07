/* ClassFlow — Auto-generated DiceBear Avatar */
'use strict';

/* ════════════════════════════════════════════
   AVATAR — DiceBear pixel-art (unique per device)
════════════════════════════════════════════ */
const AVATAR_STYLES = [
    'pixel-art',
    'adventurer-neutral',
    'bottts-neutral',
    'fun-emoji',
    'lorelei-neutral',
    'thumbs',
];

function initAvatar() {
    // Retrieve or generate a permanent random seed + style
    let seed  = localStorage.getItem('cf-avatar-seed');
    let style = localStorage.getItem('cf-avatar-style');

    if (!seed) {
        // Random 12-char seed — unique per device/browser
        seed  = Math.random().toString(36).slice(2, 14) +
                Math.random().toString(36).slice(2, 8);
        // Pick a random style from the curated list
        style = AVATAR_STYLES[Math.floor(Math.random() * AVATAR_STYLES.length)];
        localStorage.setItem('cf-avatar-seed',  seed);
        localStorage.setItem('cf-avatar-style', style);
    }

    const url = `https://api.dicebear.com/9.x/${style}/svg?seed=${seed}&size=128&backgroundColor=b6e3f4,c0aede,d1d4f9,ffd5dc,ffdfbf`;

    // Apply to both small (topbar) and large (profile dropdown)
    ['avatar-img-sm', 'avatar-img-lg'].forEach(imgId => {
        const img = id(imgId);
        if (!img) return;
        img.src = url;
        img.onerror = () => {
            // Fallback: hide broken img, show letter
            img.style.display = 'none';
            img.parentElement.insertAdjacentText('beforeend', 'S');
        };
    });
}
