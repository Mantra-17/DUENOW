/* ClassFlow — App Core: Init, Navigation, Task Actions */
'use strict';

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

// Alias used by clearNotifRead() in notifications.js
function showToast(msg) { toast(msg); }

/* ════════════════════════════════════════════
   SEARCH
════════════════════════════════════════════ */
let _st;
id('search-inp').addEventListener('input', function() {
    clearTimeout(_st);
    S.search = this.value.trim();
    _st = setTimeout(() => {
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

/* ════════════════════════════════════════════
   PWA INSTALL PROMPT
   ════════════════════════════════════════════ */
let deferredPrompt = null;

function initInstallPrompt() {
    window.addEventListener('beforeinstallprompt', (e) => {
        // Prevent the mini-infobar from appearing on mobile
        e.preventDefault();
        // Stash the event so it can be triggered later.
        deferredPrompt = e;
        
        // Check if user dismissed it in the last 7 days
        const dismissedTime = localStorage.getItem('cf-pwa-dismissed');
        if (dismissedTime) {
            const daysSinceDismissal = (Date.now() - parseInt(dismissedTime, 10)) / (1000 * 60 * 60 * 24);
            if (daysSinceDismissal < 7) {
                return; // Don't show within 7 days
            }
        }
        
        // Show the install banner after 30 seconds
        setTimeout(showInstallBanner, 30000);
    });

    window.addEventListener('appinstalled', (evt) => {
        deferredPrompt = null;
        toast('ClassFlow installed successfully! 🎉');
        hideInstallBanner();
    });
}

window.showInstallBanner = function() {
    if (!deferredPrompt) return;
    
    // Create banner element if it doesn't exist
    let banner = id('pwa-install-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'pwa-install-banner';
        banner.className = 'pwa-banner';
        banner.innerHTML = `
            <div class="pwa-banner-icon">
                <span class="material-icons-round">rocket_launch</span>
            </div>
            <div class="pwa-banner-content">
                <div class="pwa-banner-title">Install ClassFlow</div>
                <div class="pwa-banner-desc">Get the full app experience with standalone window and offline access.</div>
            </div>
            <div class="pwa-banner-actions">
                <button class="btn btn-ghost" onclick="dismissInstallBanner()">Not now</button>
                <button class="btn btn-fill" onclick="triggerPwaInstall()">Install</button>
            </div>
        `;
        document.body.appendChild(banner);
    }
    
    // Force reflow
    banner.offsetWidth;
    banner.classList.add('show');
};

window.hideInstallBanner = function() {
    const banner = id('pwa-install-banner');
    if (banner) {
        banner.classList.remove('show');
    }
};

window.dismissInstallBanner = function() {
    window.hideInstallBanner();
    localStorage.setItem('cf-pwa-dismissed', Date.now().toString());
    deferredPrompt = null;
};

window.triggerPwaInstall = async function() {
    if (!deferredPrompt) return;
    window.hideInstallBanner();
    
    // Show the install prompt
    deferredPrompt.prompt();
    
    // Wait for the user to respond to the prompt
    const { outcome } = await deferredPrompt.userChoice;
    console.log(`User response to the install prompt: ${outcome}`);
    
    // We've used the prompt, and can't use it again
    deferredPrompt = null;
};

(async function init() {
    initTheme();
    initAvatar();
    initInstallPrompt();
    if (window.checkPwaPushPrompt) {
        window.checkPwaPushPrompt();
    }
    await refreshAll();
    if (window.checkPushSubscriptionState) {
        await window.checkPushSubscriptionState();
    }
})();
