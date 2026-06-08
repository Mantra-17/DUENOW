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

const GRADIENTS = [
    'linear-gradient(135deg, #FF512F, #DD2476)', // Sunset Glow
    'linear-gradient(135deg, #00c6ff, #0072ff)', // Deep Ocean
    'linear-gradient(135deg, #8A2387, #E94057, #F27121)', // Sweet Purple
    'linear-gradient(135deg, #11998e, #38ef7d)', // Fresh Mint
    'linear-gradient(135deg, #654ea3, #eaafc8)', // Royal Lavender
    'linear-gradient(135deg, #833ab4, #fd1d1d, #fcb045)', // Electric Indigo
    'linear-gradient(135deg, #f12711, #f5af19)', // Warm Amber
    'linear-gradient(135deg, #1D976C, #93F9B9)', // Sleek Teal
    'linear-gradient(135deg, #DA4453, #89216B)', // Modern Plum
    'linear-gradient(135deg, #4f46e5, #06b6d4)'  // Indigo/Cyan
];

function applyAvatarGradient(el, seed) {
    if (!el || !seed) return;
    let hash = 0;
    for (let i = 0; i < seed.length; i++) {
        hash = seed.charCodeAt(i) + ((hash << 5) - hash);
    }
    const idx = Math.abs(hash) % GRADIENTS.length;
    el.style.background = GRADIENTS[idx];
    el.style.color = '#FFFFFF';
}
window.applyAvatarGradient = applyAvatarGradient;

function initAvatar() {
    let seed  = localStorage.getItem('cf-avatar-seed');
    let style = localStorage.getItem('cf-avatar-style');

    if (!seed) {
        seed  = Math.random().toString(36).slice(2, 14) +
                Math.random().toString(36).slice(2, 8);
        style = AVATAR_STYLES[Math.floor(Math.random() * AVATAR_STYLES.length)];
        localStorage.setItem('cf-avatar-seed',  seed);
        localStorage.setItem('cf-avatar-style', style);
    }

    const url = `https://api.dicebear.com/9.x/${style}/svg?seed=${seed}&size=128&backgroundColor=b6e3f4,c0aede,d1d4f9,ffd5dc,ffdfbf`;

    const cachedUser = localStorage.getItem('cf_cached_user');
    let initial = 'S';
    let userSeed = seed;
    if (cachedUser) {
        try {
            const user = JSON.parse(cachedUser);
            initial = getCleanInitial(user.name, user.email);
            userSeed = user.email || user.id || seed;
        } catch(e) {}
    }

    ['avatar-img-sm', 'avatar-img-lg'].forEach(imgId => {
        const img = id(imgId);
        if (!img) return;
        
        // Apply gradient to the parent button container by default
        if (img.parentElement) {
            applyAvatarGradient(img.parentElement, userSeed);
        }
        
        img.src = url;
        img.onerror = () => {
            img.style.display = 'none';
            Array.from(img.parentElement.childNodes).forEach(node => {
                if (node.nodeType === Node.TEXT_NODE) node.remove();
            });
            img.parentElement.appendChild(document.createTextNode(initial));
        };
    });
}
