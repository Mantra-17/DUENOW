'use strict';
/* ClassFlow — In-App Notification System */

/* ════════════════════════════════════════════
   ICONS + LABELS
════════════════════════════════════════════ */
const NOTIF_ICON = {
    new:      'assignment_add',
    overdue:  'warning',
    today:    'alarm',
    tomorrow: 'alarm_on',
    '3days':  'schedule',
    '5days':  'event_upcoming',
};

const CLS_LABEL = { Assignment:'Assignment', CIE:'CIE Exam', Practical:'Practical', Project:'Project', Other:'Task' };
const CLS_ICO   = { Assignment:'assignment', CIE:'quiz', Practical:'science', Project:'rocket_launch', Other:'task_alt' };

/* ════════════════════════════════════════════
   TYPE A — NEW TASK DETECTION
   Stored permanently in localStorage
════════════════════════════════════════════ */
function processNewTasks(tasks) {
    const seenIds   = new Set(JSON.parse(localStorage.getItem('cf-seen-ids') || '[]'));
    const newAlerts = JSON.parse(localStorage.getItem('cf-notif-new') || '[]');
    let   changed   = false;

    tasks.forEach(task => {
        const key = String(task.id);
        if (!seenIds.has(key)) {
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

    while (newAlerts.length > 40) newAlerts.pop();
    localStorage.setItem('cf-seen-ids',  JSON.stringify([...seenIds]));
    localStorage.setItem('cf-notif-new', JSON.stringify(newAlerts));
    return changed;
}

function getNewAlerts() {
    // Only return alerts that haven't been individually dismissed
    const dismissed = getDismissedSet();
    return JSON.parse(localStorage.getItem('cf-notif-new') || '[]')
        .filter(n => !dismissed.has(n.id));
}

/* ════════════════════════════════════════════
   TYPE B — DUE DATE REMINDERS
   Computed live; can be individually dismissed
════════════════════════════════════════════ */
function getDueDateAlerts() {
    if (!S.tasks || !S.tasks.length) return [];
    const dismissed = getDismissedSet();
    const alerts = [];

    S.tasks.forEach(task => {
        if (task.is_completed || !task.due_date) return;
        const d = daysDiff(task.due_date);
        if (d === null) return;

        let alert = null;
        if      (d < 0)   alert = { id:'ov-'+task.id, type:'overdue',  label:`Overdue by ${Math.abs(d)} day${Math.abs(d)>1?'s':''}`, task };
        else if (d === 0) alert = { id:'td-'+task.id, type:'today',    label:'Due Today!',       task };
        else if (d === 1) alert = { id:'tm-'+task.id, type:'tomorrow', label:'Due Tomorrow',     task };
        else if (d <= 3)  alert = { id:'3d-'+task.id, type:'3days',    label:`Due in ${d} days`, task };
        else if (d <= 5)  alert = { id:'5d-'+task.id, type:'5days',    label:`Due in ${d} days`, task };

        if (alert && !dismissed.has(alert.id)) alerts.push(alert);
    });

    const ord = { overdue:0, today:1, tomorrow:2, '3days':3, '5days':4 };
    alerts.sort((a,b) => ord[a.type] - ord[b.type]);
    return alerts;
}

/* ════════════════════════════════════════════
   STORAGE HELPERS
════════════════════════════════════════════ */
function getDismissedSet()        { return new Set(JSON.parse(localStorage.getItem('cf-notif-dismissed') || '[]')); }
function saveDismissedSet(s)      { localStorage.setItem('cf-notif-dismissed', JSON.stringify([...s])); }

function allNotifIds() {
    return [
        ...getNewAlerts().map(n => n.id),
        ...getDueDateAlerts().map(n => n.id),
    ];
}

/* ════════════════════════════════════════════
   BADGE
════════════════════════════════════════════ */
function updateNotifBadge() {
    const badge = id('notif-badge');
    if (!badge) return;
    const count = allNotifIds().length;   // every visible notif counts as unread until dismissed
    badge.textContent = count > 9 ? '9+' : String(count);
    badge.classList.toggle('hidden', count === 0);
}

/* ════════════════════════════════════════════
   PANEL OPEN / CLOSE
   — does NOT auto-mark-read on close
════════════════════════════════════════════ */
function toggleNotif() {
    id('notif-panel').classList.contains('open') ? closeNotif() : openNotif();
}

function openNotif() {
    renderNotifPanel();
    id('notif-panel').classList.add('open');
    setTimeout(() => document.addEventListener('click', outsideNotifClick), 0);
}

function closeNotif() {
    id('notif-panel').classList.remove('open');
    document.removeEventListener('click', outsideNotifClick);
    updateNotifBadge();
}

function outsideNotifClick(e) {
    if (!id('notif-wrap').contains(e.target)) closeNotif();
}

/* ════════════════════════════════════════════
   RENDER PANEL
   Only shows non-dismissed notifications
════════════════════════════════════════════ */
function renderNotifPanel() {
    const newAlerts = getNewAlerts();
    const dueAlerts = getDueDateAlerts();
    const el        = id('notif-list');

    if (!newAlerts.length && !dueAlerts.length) {
        el.innerHTML = `
        <div class="notif-empty">
            <span class="material-icons-round">notifications_none</span>
            <p>You're all caught up!<br>No new tasks or upcoming deadlines.</p>
        </div>`;
        updateNotifBadge();
        return;
    }

    let html = '';

    /* ── Section A: New from Classroom ── */
    if (newAlerts.length) {
        html += `<div class="notif-section-hdr">New from Classroom</div>`;
        html += newAlerts.map(n => {
            const cls    = n.classification || 'Other';
            const ico    = CLS_ICO[cls]  || 'task_alt';
            const clsLbl = CLS_LABEL[cls] || 'Task';
            const sub    = n.subject ? shortSub(n.subject) : '';
            const ago    = timeAgo(n.ts);
            return `
            <div class="notif-item unread" id="ni-${n.id}">
                <div class="notif-ico ni-new" onclick="notifClick('${n.taskId}','${n.id}')" style="cursor:pointer">
                    <span class="material-icons-round">${ico}</span>
                </div>
                <div class="notif-body" onclick="notifClick('${n.taskId}','${n.id}')" style="cursor:pointer">
                    <div class="notif-title">${esc(n.title)}</div>
                    <div class="notif-sub">${esc(clsLbl)} assigned · ${esc(sub)}</div>
                    <div class="notif-tap">Tap to view</div>
                </div>
                <div class="notif-time">${esc(ago)}</div>
                <button class="notif-dismiss" onclick="dismissNotif('${n.id}',event)" title="Dismiss" aria-label="Dismiss notification">
                    <span class="material-icons-round">close</span>
                </button>
            </div>`;
        }).join('');
    }

    /* ── Section B: Deadline Reminders ── */
    if (dueAlerts.length) {
        html += `<div class="notif-section-hdr">Deadline Reminders</div>`;
        html += dueAlerts.map(n => {
            const ico = NOTIF_ICON[n.type] || 'schedule';
            const sub = n.task.subject ? shortSub(n.task.subject) : '';
            return `
            <div class="notif-item unread" id="ni-${n.id}">
                <div class="notif-ico ni-${n.type}" onclick="notifClick('${n.task.id}','${n.id}')" style="cursor:pointer">
                    <span class="material-icons-round">${ico}</span>
                </div>
                <div class="notif-body" onclick="notifClick('${n.task.id}','${n.id}')" style="cursor:pointer">
                    <div class="notif-title">${esc(n.task.title)}</div>
                    <div class="notif-sub">${esc(n.label)} · ${esc(sub)}</div>
                </div>
                <button class="notif-dismiss" onclick="dismissNotif('${n.id}',event)" title="Dismiss" aria-label="Dismiss notification">
                    <span class="material-icons-round">close</span>
                </button>
            </div>`;
        }).join('');
    }

    el.innerHTML = html;
}

/* ════════════════════════════════════════════
   ACTIONS
════════════════════════════════════════════ */

/* Click a notification → dismiss it + navigate to the task */
function notifClick(taskId, notifId) {
    dismissNotif(notifId);           // removes from panel immediately
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

/* Dismiss a single notification (X button or on click) */
function dismissNotif(notifId, evt) {
    if (evt) evt.stopPropagation();

    const s = getDismissedSet();
    s.add(notifId);
    saveDismissedSet(s);

    // Also remove from cf-notif-new if it's a new-task alert
    const stored = JSON.parse(localStorage.getItem('cf-notif-new') || '[]');
    const filtered = stored.filter(n => n.id !== notifId);
    localStorage.setItem('cf-notif-new', JSON.stringify(filtered));

    // Animate out then re-render
    const el = id('ni-' + notifId);
    if (el) {
        el.style.transition = 'opacity .18s, transform .18s';
        el.style.opacity    = '0';
        el.style.transform  = 'translateX(16px)';
        setTimeout(() => renderNotifPanel(), 200);
    } else {
        renderNotifPanel();
    }
    updateNotifBadge();
}

/* Mark all read = dismiss everything currently visible */
function markAllNotifsRead() {
    const s = getDismissedSet();
    allNotifIds().forEach(nid => s.add(nid));
    saveDismissedSet(s);

    // Also wipe cf-notif-new
    localStorage.removeItem('cf-notif-new');

    renderNotifPanel();
    updateNotifBadge();
    toast('All notifications cleared ✓');
}

/* Clear = same as mark all read */
function clearNotifRead() {
    markAllNotifsRead();
}

/* ════════════════════════════════════════════
   TIME AGO
════════════════════════════════════════════ */
function timeAgo(ts) {
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 60)    return 'just now';
    if (sec < 3600)  return `${Math.floor(sec/60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec/3600)}h ago`;
    return `${Math.floor(sec/86400)}d ago`;
}
