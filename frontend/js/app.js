'use strict';
/* ClassFlow — App Core: Init, Navigation, Task Actions */

/* ════════════════════════════════════════════
   CONFIG
════════════════════════════════════════════ */
const API_KEY      = 'your_secret_api_key_here';
const HEADERS      = { 'X-API-KEY': API_KEY, 'Content-Type': 'application/json' };
const REFRESH_MS   = 60_000;

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


/* ════════════════════════════════════════════
   API
════════════════════════════════════════════ */
async function api(path, opts = {}) {
    const r = await fetch(path, { headers: HEADERS, ...opts });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || `HTTP ${r.status}`); }
    return r.json();
}

/* ════════════════════════════════════════════
   DATA
════════════════════════════════════════════ */
async function refreshAll() {
    const ico = id('refresh-ico');
    ico.style.animation = 'spinR .7s linear infinite';
    hideErr();
    try {
        const [tasks, stats, subjects] = await Promise.all([
            api('/tasks?sort=due_date&order=asc&limit=500').then(d => d.tasks || []),
            api('/stats'),
            api('/subjects').then(d => d.subjects || []),
        ]);
        S.tasks    = tasks;
        S.stats    = stats;
        S.subjects = subjects;
        processNewTasks(tasks);   // detect newly synced Classroom tasks
        renderAll();
    } catch(e) {
        showErr(e.message);
    } finally {
        ico.style.animation = '';
    }
}

/* ════════════════════════════════════════════
   NAVIGATION
════════════════════════════════════════════ */
function show(v) {
    S.view = v;
    // Desktop nav
    document.querySelectorAll('.nav-item[id^="n-"]').forEach(el => {
        el.classList.remove('active');
        el.removeAttribute('aria-current');
    });
    const nel = id('n-' + v);
    if (nel) { nel.classList.add('active'); nel.setAttribute('aria-current','page'); }
    // Mobile nav
    document.querySelectorAll('.mob-btn').forEach(el => el.classList.remove('on'));
    const mel = id('mb-' + v);
    if (mel) mel.classList.add('on');
    // Views
    document.querySelectorAll('.view').forEach(el => el.classList.remove('show'));
    const vel = id('v-' + v);
    if (vel) vel.classList.add('show');

    if (v === 'dashboard') renderDash();
    if (v === 'tasks')     renderTasks();
    if (v === 'subjects')  renderSubjects();
    if (v === 'done')      renderDone();
}

function quickFilter(type) { show('tasks'); setFilter(type); }

/* ════════════════════════════════════════════
   FILTER + SORT
════════════════════════════════════════════ */
function setFilter(f) {
    S.filter = f;
    document.querySelectorAll('.chip[data-f]').forEach(el =>
        el.classList.toggle('on', el.dataset.f === f)
    );
    renderTasks();
}

function sorted(arr, key) {
    const a = [...arr];
    if (key === 'due_date') {
        a.sort((x,y) => {
            if (!x.due_date && !y.due_date) return 0;
            if (!x.due_date) return 1;
            if (!y.due_date) return -1;
            return x.due_date.localeCompare(y.due_date);
        });
    } else if (key === 'difficulty' || key === 'estimated_minutes') {
        a.sort((x,y) => (y[key]||0) - (x[key]||0));
    } else {
        a.sort((x,y) => (y.created_at||'').localeCompare(x.created_at||''));
    }
    return a;
}

function filterSubject(sub) {
    S.search = sub;
    id('search-inp').value = sub;
    S.filter = 'all';
    setFilter('all');
    show('tasks');
}

/* ════════════════════════════════════════════
   ACTIONS
════════════════════════════════════════════ */
async function toggleDone(tid, isDone) {
    const c = id('card-'+tid);
    if (c) c.style.opacity = '.45';
    try {
        await api(`/tasks/${tid}/${isDone?'uncomplete':'complete'}`, { method:'POST' });
        const t = S.tasks.find(x => x.id === tid);
        if (t) t.is_completed = !isDone;
        renderAll();
        toast(isDone ? 'Marked as pending' : 'Marked as completed ✓');
    } catch(e) {
        if (c) c.style.opacity = '1';
        toast(e.message, true);
    }
}

async function delTask(tid) {
    if (!confirm('Permanently delete this assignment?')) return;
    const c = id('card-'+tid);
    if (c) { c.style.opacity='0'; c.style.transform='translateX(20px)'; c.style.transition='all .25s'; }
    try {
        await api(`/tasks/${tid}`, { method:'DELETE' });
        S.tasks = S.tasks.filter(t => t.id !== tid);
        setTimeout(renderAll, 280);
        toast('Assignment deleted');
    } catch(e) {
        if (c) { c.style.opacity='1'; c.style.transform=''; }
        toast(e.message, true);
    }
}

function toggleSummary(tid) {
    const el = id('sum-'+tid);
    if (el) el.classList.toggle('open');
}

/* ════════════════════════════════════════════
   MODAL
════════════════════════════════════════════ */
function openModal() {
    id('modal-bg').classList.add('open');
    document.body.style.overflow = 'hidden';
    id('f-title').focus();

    const sel = id('f-subject');
    if (sel.options.length <= 1) {
        S.subjects.forEach(s => {
            const o = document.createElement('option');
            o.value = s.subject;
            o.textContent = s.subject.length > 58 ? s.subject.slice(0,56)+'…' : s.subject;
            sel.appendChild(o);
        });
    }
}

function closeModal() {
    id('modal-bg').classList.remove('open');
    document.body.style.overflow = '';
    id('add-form').reset();
    const b = id('submit-btn');
    b.disabled = false;
    b.innerHTML = '<span class="material-icons-round" style="font-size:17px">auto_awesome</span> Analyze &amp; Add';
}

function bgClick(e) { if (e.target === id('modal-bg')) closeModal(); }

async function submitTask(e) {
    e.preventDefault();
    const title   = id('f-title').value.trim();
    const subject = id('f-subject').value.trim();
    const dueDate = id('f-due').value;

    if (!title || !subject) { toast('Title and subject are required', true); return; }

    const b = id('submit-btn');
    b.disabled = true;
    b.innerHTML = '<div class="spin"></div> Analyzing…';

    try {
        const body = { title, subject };
        if (dueDate) body.due_date = dueDate;
        const result = await api('/tasks', { method:'POST', body: JSON.stringify(body) });
        S.tasks.push(result.task);
        S.tasks.sort((a,b) => {
            if (!a.due_date && !b.due_date) return 0;
            if (!a.due_date) return 1;
            if (!b.due_date) return -1;
            return a.due_date.localeCompare(b.due_date);
        });
        closeModal();
        renderAll();
        toast(`"${title}" added ✓`);
        setTimeout(() => {
            const nc = id('card-'+result.task.id);
            if (nc) nc.scrollIntoView({ behavior:'smooth', block:'center' });
        }, 400);
    } catch(err) {
        b.disabled = false;
        b.innerHTML = '<span class="material-icons-round" style="font-size:17px">auto_awesome</span> Analyze &amp; Add';
        toast(err.message, true);
    }
}

/* ════════════════════════════════════════════
   ERROR + TOAST
════════════════════════════════════════════ */
function showErr(msg) {
    const b = id('err-banner');
    b.style.display = 'flex';
    id('err-msg').textContent = 'API error: ' + msg;
}

function hideErr() { id('err-banner').style.display = 'none'; }

function toast(msg, isErr = false) {
    const c  = id('toasts');
    const el = document.createElement('div');
    el.className = 'toast' + (isErr ? ' err' : '');
    el.innerHTML = `<span class="material-icons-round">${isErr?'error_outline':'check_circle'}</span>${esc(msg)}`;
    c.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateX(18px)';
        el.style.transition = 'all .3s';
        setTimeout(() => el.remove(), 300);
    }, 3200);
}

/* ════════════════════════════════════════════
   SEARCH
════════════════════════════════════════════ */
let st;
id('search-inp').addEventListener('input', function() {
    clearTimeout(st);
    S.search = this.value.trim();
    st = setTimeout(() => {
        if (S.search && S.view !== 'tasks') show('tasks');
        else renderTasks();
    }, 200);
});

/* ════════════════════════════════════════════
   KEYBOARD SHORTCUTS
════════════════════════════════════════════ */
document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); id('search-inp').focus(); }
    if (e.key === 'Escape') closeModal();
});

/* ════════════════════════════════════════════
   AUTO-REFRESH + INIT
════════════════════════════════════════════ */
setInterval(refreshAll, REFRESH_MS);

(async function init() {
    initTheme();
    initAvatar();   // generate unique avatar immediately
    await refreshAll();
})();
