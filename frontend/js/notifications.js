'use strict';
/* ClassFlow — In-App Notification System */

/* ════════════════════════════════════════════
   NOTIFICATIONS — Two types:
     A) New-task alerts  (persisted in localStorage)
     B) Due-date reminders (computed live from task data)
════════════════════════════════════════════ */

// Icons per type
const NOTIF_ICON = {
    new:      'assignment_add',
    overdue:  'warning',
    today:    'alarm',
    tomorrow: 'alarm_on',
    '3days':  'schedule',
    '5days':  'event_upcoming',
};

// Classification labels & icons for new-task alerts
const CLS_LABEL = { Assignment:'Assignment', CIE:'CIE Exam', Practical:'Practical', Project:'Project', Other:'Task' };
const CLS_ICO   = { Assignment:'assignment', CIE:'quiz',      Practical:'science',  Project:'rocket_launch', Other:'task_alt' };

/* ────────────────────────────────────────────
   TYPE A — NEW TASK DETECTION
──────────────────────────────────────────── */
function processNewTasks(tasks) {
    // "Seen" = task IDs the app has already processed
    const seenIds  = new Set(JSON.parse(localStorage.getItem('cf-seen-ids')  || '[]'));
    const newAlerts = JSON.parse(localStorage.getItem('cf-notif-new') || '[]');
    let   changed  = false;

    tasks.forEach(task => {
        const key = String(task.id);
        if (!seenIds.has(key)) {
            // Brand-new task — create a persistent alert
            const alertId = 'new-' + key;
            if (!newAlerts.find(a => a.id === alertId)) {
                newAlerts.unshift({
                    id:             alertId,
                    type:           'new',
                    taskId:         task.id,
                    title:          task.title,
                    classification: task.classification || 'Other',
                    subject:        task.subject || '',
                    due_date:       task.due_date || null,
                    ts:             Date.now(),
                });
                changed = true;
            }
            seenIds.add(key);
        }
    });

    // Keep only 40 most-recent new-task alerts
    while (newAlerts.length > 40) newAlerts.pop();

    localStorage.setItem('cf-seen-ids',  JSON.stringify([...seenIds]));
    localStorage.setItem('cf-notif-new', JSON.stringify(newAlerts));
    return changed;
}

function getNewAlerts() {
    return JSON.parse(localStorage.getItem('cf-notif-new') || '[]');
}

/* ────────────────────────────────────────────
   TYPE B — DUE DATE REMINDERS
──────────────────────────────────────────── */
function getDueDateAlerts() {
    if (!S.tasks || !S.tasks.length) return [];
    const alerts = [];
    S.tasks.forEach(task => {
        if (task.is_completed || !task.due_date) return;
        const d = daysDiff(task.due_date);
        if (d === null) return;
        if      (d < 0)  alerts.push({ id:'ov-'+task.id, type:'overdue',  label:`Overdue by ${Math.abs(d)} day${Math.abs(d)>1?'s':''}`, task });
        else if (d === 0) alerts.push({ id:'td-'+task.id, type:'today',    label:'Due Today!',      task });
        else if (d === 1) alerts.push({ id:'tm-'+task.id, type:'tomorrow', label:'Due Tomorrow',    task });
        else if (d <= 3)  alerts.push({ id:'3d-'+task.id, type:'3days',   label:`Due in ${d} days`, task });
        else if (d <= 5)  alerts.push({ id:'5d-'+task.id, type:'5days',   label:`Due in ${d} days`, task });
    });
    const ord = { overdue:0, today:1, tomorrow:2, '3days':3, '5days':4 };
    alerts.sort((a,b) => ord[a.type] - ord[b.type]);
    return alerts;
}

/* ────────────────────────────────────────────
   READ STATE
──────────────────────────────────────────── */
function getReadSet()       { return new Set(JSON.parse(localStorage.getItem('cf-notif-read') || '[]')); }
function saveReadSet(s)     { localStorage.setItem('cf-notif-read', JSON.stringify([...s])); }

function allNotifIds() {
    return [
        ...getNewAlerts().map(n => n.id),
        ...getDueDateAlerts().map(n => n.id),
    ];
}

function updateNotifBadge() {
    const badge = id('notif-badge');
    if (!badge) return;
    const read   = getReadSet();
    const unread = allNotifIds().filter(nid => !read.has(nid)).length;
    badge.textContent = unread > 9 ? '9+' : String(unread);
    badge.classList.toggle('hidden', unread === 0);
}

/* ────────────────────────────────────────────
   PANEL OPEN / CLOSE
──────────────────────────────────────────── */
function toggleNotif() {
    id('notif-panel').classList.contains('open') ? closeNotif() : openNotif();
}

function openNotif() {
    renderNotifPanel();
    id('notif-panel').classList.add('open');
    setTimeout(() => document.addEventListener('click', outsideNotifClick), 0);
}

function closeNotif() {
    // Auto-mark everything as read when panel is closed
    const s = getReadSet();
    allNotifIds().forEach(nid => s.add(nid));
    saveReadSet(s);
    id('notif-panel').classList.remove('open');
    document.removeEventListener('click', outsideNotifClick);
    updateNotifBadge();
}

function outsideNotifClick(e) {
    if (!id('notif-wrap').contains(e.target)) closeNotif();
}

/* ────────────────────────────────────────────
   RENDER PANEL
──────────────────────────────────────────── */
function renderNotifPanel() {
    const read       = getReadSet();
    const newAlerts  = getNewAlerts();
    const dueAlerts  = getDueDateAlerts();
    const el         = id('notif-list');

    if (!newAlerts.length && !dueAlerts.length) {
        el.innerHTML = `<div class="notif-empty">
            <span class="material-icons-round">notifications_none</span>
            <p>You're all caught up!<br>No new tasks or upcoming deadlines.</p>
        </div>`;
        return;
    }

    let html = '';

    // ── Section A: New Tasks from Classroom ──
    if (newAlerts.length) {
        html += `<div class="notif-section-hdr">New from Classroom</div>`;
        html += newAlerts.map(n => {
            const unread = !read.has(n.id);
            const cls    = n.classification || 'Other';
            const ico    = CLS_ICO[cls]  || 'task_alt';
            const clsLbl = CLS_LABEL[cls] || 'Task';
            const sub    = n.subject ? shortSub(n.subject) : '';
            const ago    = timeAgo(n.ts);
            return `<div class="notif-item ${unread ? 'unread' : ''}" onclick="notifClick('${n.taskId}','${n.id}')">
                <div class="notif-ico ni-new"><span class="material-icons-round">${ico}</span></div>
                <div class="notif-body">
                    <div class="notif-title">${esc(n.title)}</div>
                    <div class="notif-sub">${esc(clsLbl)} assigned · ${esc(sub)}</div>
                    <div class="notif-tap">Tap to view</div>
                </div>
                <div class="notif-time">${esc(ago)}</div>
            </div>`;
        }).join('');
    }

    // ── Section B: Due Date Reminders ──
    if (dueAlerts.length) {
        html += `<div class="notif-section-hdr">Deadline Reminders</div>`;
        html += dueAlerts.map(n => {
            const unread = !read.has(n.id);
            const ico    = NOTIF_ICON[n.type] || 'schedule';
            const sub    = n.task.subject ? shortSub(n.task.subject) : '';
            return `<div class="notif-item ${unread ? 'unread' : ''}" onclick="notifClick('${n.task.id}','${n.id}')">
                <div class="notif-ico ni-${n.type}"><span class="material-icons-round">${ico}</span></div>
                <div class="notif-body">
                    <div class="notif-title">${esc(n.task.title)}</div>
                    <div class="notif-sub">${esc(n.label)} · ${esc(sub)}</div>
                </div>
            </div>`;
        }).join('');
    }

    el.innerHTML = html;
}

/* ────────────────────────────────────────────
   HELPERS
──────────────────────────────────────────── */
function notifClick(taskId, notifId) {
    const s = getReadSet(); s.add(notifId); saveReadSet(s);
    closeNotif();
    show('dashboard');
    setTimeout(() => {
        const card = id('card-' + taskId);
        if (!card) return;
        card.scrollIntoView({ behavior:'smooth', block:'center' });
        card.style.transition = 'box-shadow .3s';
        card.style.boxShadow  = '0 0 0 3px var(--primary)';
        setTimeout(() => card.style.boxShadow = '', 1800);
    }, 320);
}

function markAllNotifsRead() {
    const s = new Set(allNotifIds());
    saveReadSet(s);
    renderNotifPanel();
    updateNotifBadge();
}

function clearNotifRead() {
    // Permanently dismiss all "New from Classroom" alerts
    localStorage.removeItem('cf-notif-new');
    // Remove their IDs from read-set too (clean slate)
    const s = getReadSet();
    JSON.parse(localStorage.getItem('cf-notif-new') || '[]').forEach(n => s.delete(n.id));
    saveReadSet(s);
    renderNotifPanel();
    updateNotifBadge();
    showToast('Notifications cleared', 'check_circle');
}

function timeAgo(ts) {
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 60)   return 'just now';
    if (sec < 3600) return `${Math.floor(sec/60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec/3600)}h ago`;
    return `${Math.floor(sec/86400)}d ago`;
}
