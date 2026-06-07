'use strict';
/* ClassFlow — Render Functions */

/* ════════════════════════════════════════════
   RENDER: NAV BADGES
════════════════════════════════════════════ */
function renderNavBadges() {
    const pending = S.tasks.filter(t => !t.is_completed);
    const done    = S.tasks.filter(t => t.is_completed);
    setText('nb-dashboard', pending.length);
    setText('nb-tasks',     pending.length);
    setText('nb-subjects',  S.subjects.length);
    setText('nb-done',      done.length);
    setText('nb-cie',       pending.filter(t => t.classification === 'CIE').length);
    setText('nb-practical', pending.filter(t => t.classification === 'Practical').length);
    setText('nb-project',   pending.filter(t => t.classification === 'Project').length);
}

/* ════════════════════════════════════════════
   RENDER: STATS
════════════════════════════════════════════ */
function renderStats() {
    if (!S.stats) return;
    const urgent = S.tasks.filter(t => {
        if (t.is_completed || !t.due_date) return false;
        const d = daysDiff(t.due_date);
        return d !== null && d <= 3;
    }).length;

    const hours = parseFloat(S.stats.total_est_hours || 0).toFixed(1);
    id('stats-row').innerHTML = `
        <div class="stat-card"><div class="stat-ico ico-blue"><span class="material-icons-round">assignment</span></div>
            <div><div class="stat-val" data-t="${S.stats.total_tasks||0}">0</div><div class="stat-lbl">Total Tasks</div></div></div>
        <div class="stat-card"><div class="stat-ico ico-red"><span class="material-icons-round">alarm</span></div>
            <div><div class="stat-val" data-t="${urgent}">0</div><div class="stat-lbl">Due in ≤ 3 Days</div></div></div>
        <div class="stat-card"><div class="stat-ico ico-green"><span class="material-icons-round">task_alt</span></div>
            <div><div class="stat-val" data-t="${S.stats.completed_tasks||0}">0</div><div class="stat-lbl">Completed</div></div></div>
        <div class="stat-card"><div class="stat-ico ico-amber"><span class="material-icons-round">schedule</span></div>
            <div><div class="stat-val" data-t="${hours}" data-suf="h">0</div><div class="stat-lbl">Study Hours</div></div></div>`;
    countUp();
}

function countUp() {
    document.querySelectorAll('.stat-val[data-t]').forEach(el => {
        const target = parseFloat(el.dataset.t);
        const suf    = el.dataset.suf || '';
        const isF    = !Number.isInteger(target);
        const dur    = 750;
        const t0     = performance.now();
        (function tick(now) {
            const p = Math.min((now - t0) / dur, 1);
            const e = 1 - Math.pow(1 - p, 3);
            const c = target * e;
            el.textContent = (isF ? c.toFixed(1) : Math.round(c)) + suf;
            if (p < 1) requestAnimationFrame(tick);
        })(t0);
    });
}

/* ════════════════════════════════════════════
   RENDER: AI PROGRESS BAR
════════════════════════════════════════════ */
function renderAiBar() {
    const total = S.tasks.length;
    const done  = S.tasks.filter(t => t.ai_success).length;
    if (done < total && total > 0) {
        id('ai-bar').style.display = 'flex';
        const pct = Math.round(done / total * 100);
        id('ai-fill').style.width = pct + '%';
        id('ai-txt').textContent  = `AI analyzing: ${done}/${total} tasks (${pct}%)`;
    } else {
        id('ai-bar').style.display = 'none';
    }
}

/* ════════════════════════════════════════════
   RENDER: DASHBOARD
════════════════════════════════════════════ */
function renderDash() {
    renderStats();
    renderAiBar();

    // Urgent
    const urgent = S.tasks.filter(t => {
        if (t.is_completed || !t.due_date) return false;
        const d = daysDiff(t.due_date);
        return d !== null && d <= 3;
    });
    const uw = id('urgent-wrap');
    if (urgent.length) {
        uw.style.display = 'block';
        id('urgent-list').innerHTML = urgent.map((t,i) => card(t, i*35)).join('');
    } else {
        uw.style.display = 'none';
    }

    // All pending
    const sk  = id('sort-dash').value;
    const all = sorted(S.tasks.filter(t => !t.is_completed), sk);
    setText('dash-count', all.length + ' pending');
    id('dash-list').innerHTML = all.length
        ? all.map((t,i) => card(t, i*25)).join('')
        : empty('task_alt', 'All caught up!', 'No pending assignments — great work! 🎉');
}

/* ════════════════════════════════════════════
   RENDER: TASKS VIEW
════════════════════════════════════════════ */
function renderTasks() {
    updateChipCounts();
    let tasks = S.tasks.filter(t => !t.is_completed);

    if (S.search) {
        const q = S.search.toLowerCase();
        tasks = tasks.filter(t =>
            t.title.toLowerCase().includes(q) ||
            t.subject.toLowerCase().includes(q) ||
            (t.summary||'').toLowerCase().includes(q)
        );
    }

    if (S.filter !== 'all') {
        tasks = tasks.filter(t => (t.classification||'Other') === S.filter);
    }

    tasks = sorted(tasks, id('sort-tasks').value);
    setText('tasks-count', `${tasks.length} assignment${tasks.length!==1?'s':''}`);

    id('tasks-list').innerHTML = tasks.length
        ? tasks.map((t,i) => card(t, i*25)).join('')
        : empty('search_off', 'No results', S.filter !== 'all'
            ? `No ${S.filter} assignments. Try a different filter.`
            : 'No assignments match your search.');
}

function updateChipCounts() {
    // Base: pending tasks, narrowed by the active search (so subject filter is respected)
    let base = S.tasks.filter(t => !t.is_completed);
    if (S.search) {
        const q = S.search.toLowerCase();
        base = base.filter(t =>
            t.title.toLowerCase().includes(q) ||
            t.subject.toLowerCase().includes(q) ||
            (t.summary||'').toLowerCase().includes(q)
        );
    }
    setText('cn-all', base.length);
    ['Assignment','CIE','Practical','Project','Other'].forEach(type => {
        setText('cn-'+type, base.filter(t => (t.classification||'Other') === type).length);
    });
}

/* ════════════════════════════════════════════
   RENDER: SUBJECTS
════════════════════════════════════════════ */
function renderSubjects() {
    setText('subj-count', `${S.subjects.length} courses`);
    if (!S.subjects.length) {
        id('subj-grid').innerHTML = empty('menu_book','No subjects','Sync Classroom to load subjects.');
        return;
    }
    id('subj-grid').innerHTML = S.subjects.map((s,i) => {
        const pct    = s.total > 0 ? Math.round(s.completed / s.total * 100) : 0;
        const color  = hslColor(s.subject);
        const abbr   = subAbbr(s.subject);
        const C      = 2 * Math.PI * 18;
        const offset = C - (pct / 100) * C;
        return `
        <div class="subj-card" onclick="filterSubject('${esc(s.subject)}')" tabindex="0"
             role="button" aria-label="Filter by ${esc(s.subject)}"
             style="animation:cardIn .22s ease-out ${i*40}ms both"
             onkeydown="if(event.key==='Enter')filterSubject('${esc(s.subject)}')">
            <div class="subj-card-top">
                <div class="subj-ico" style="background:${color}">${abbr}</div>
                <div class="ring-wrap" title="${pct}% complete">
                    <svg width="44" height="44" viewBox="0 0 44 44">
                        <circle class="ring-bg" cx="22" cy="22" r="18"/>
                        <circle class="ring-fg" cx="22" cy="22" r="18"
                            stroke="${color}"
                            stroke-dasharray="${C.toFixed(1)}"
                            stroke-dashoffset="${offset.toFixed(1)}"/>
                    </svg>
                    <span class="ring-pct">${pct}%</span>
                </div>
            </div>
            <div class="subj-name">${esc(s.subject)}</div>
            <div class="subj-meta">
                <span><span class="material-icons-round">assignment</span>${s.total}</span>
                <span><span class="material-icons-round">pending_actions</span>${s.pending} pending</span>
            </div>
        </div>`;
    }).join('');
}

/* ════════════════════════════════════════════
   RENDER: COMPLETED
════════════════════════════════════════════ */
function renderDone() {
    const done = S.tasks.filter(t => t.is_completed);
    setText('done-count', `${done.length} task${done.length!==1?'s':''}`);
    id('done-list').innerHTML = done.length
        ? done.map((t,i) => card(t, i*25)).join('')
        : empty('check_circle','Nothing completed yet','Mark assignments done to track them here.');
}

function renderAll() {
    renderNavBadges();
    renderStats();
    renderAiBar();
    updateChipCounts();
    renderDash();
    renderTasks();
    renderSubjects();
    renderDone();
    updateNotifBadge();   // refresh bell count after every data update
}

/* ════════════════════════════════════════════
   TASK CARD
════════════════════════════════════════════ */
function card(t, delay = 0) {
    const cls  = t.classification || 'Other';
    const done = t.is_completed;
    const dueH = dueBadge(t.due_date);
    const subS = subChipStyle(t.subject);
    const diff = diffDots(t.difficulty || 1);
    const time = fmtMin(t.estimated_minutes);
    const ico  = clsIcon(cls);

    return `
<article class="task-card cls-${cls} ${done?'done':''}"
         id="card-${t.id}"
         style="animation-delay:${delay}ms"
         aria-label="${esc(t.title)}">
    <div class="tc-body">
        <div class="tc-top">
            <div class="tc-check ${done?'chk':''}"
                 onclick="toggleDone('${t.id}',${done})"
                 role="checkbox" aria-checked="${done}" tabindex="0"
                 onkeydown="if(event.key==='Enter'||event.key===' ')toggleDone('${t.id}',${done})">
                <span class="material-icons-round">check</span>
            </div>
            <div class="tc-content">
                <div class="tc-title">${esc(t.title)}</div>
                <div class="tc-meta">
                    <span class="sub-chip" style="${subS}" title="${esc(t.subject)}">${esc(shortSub(t.subject))}</span>
                    <span class="type-badge tb-${cls}">
                        <span class="material-icons-round">${ico}</span>${cls}
                    </span>
                    ${dueH}
                    ${!t.ai_success ? '<span class="ai-pending-lbl"><span class="material-icons-round">hourglass_empty</span>AI pending</span>' : ''}
                </div>
            </div>
            <div class="tc-acts">
                <button class="act-btn" onclick="toggleSummary('${t.id}')" title="AI Summary" aria-label="Show AI summary">
                    <span class="material-icons-round">auto_awesome</span>
                </button>
                <button class="act-btn del" onclick="delTask('${t.id}')" title="Delete" aria-label="Delete task">
                    <span class="material-icons-round">delete_outline</span>
                </button>
            </div>
        </div>
        <div class="tc-metrics">
            <div class="metric" title="Difficulty ${t.difficulty||1}/10">
                <span class="material-icons-round">bar_chart</span>
                ${diff}
                <span>${t.difficulty||'?'}/10</span>
            </div>
            <div class="metric" title="Est. study time">
                <span class="material-icons-round">schedule</span>
                <span>${time}</span>
            </div>
        </div>
    </div>
    <div class="ai-summary" id="sum-${t.id}">
        <div class="ai-summary-inner">
            <div class="ai-summary-body">
                <div class="ai-gem-ico"><span class="material-icons-round">auto_awesome</span></div>
                <div class="ai-sum-text">${esc(t.summary || 'AI summary not yet available — will update automatically.')}</div>
            </div>
        </div>
    </div>
</article>`;
}

/* ════════════════════════════════════════════
   DUE DATE
════════════════════════════════════════════ */
function daysDiff(ds) {
    if (!ds) return null;
    const now = new Date(); now.setHours(0,0,0,0);
    const due = new Date(ds + 'T00:00:00');
    return Math.round((due - now) / 86400000);
}

function dueBadge(ds) {
    if (!ds) return '<span class="due-badge due-none"><span class="material-icons-round" style="font-size:12px">calendar_today</span>No due date</span>';
    const d   = daysDiff(ds);
    const fmt = new Date(ds+'T00:00:00').toLocaleDateString('en-IN', {day:'numeric',month:'short',year:'numeric'});
    if (d < 0)   return `<span class="due-badge due-overdue"><span class="material-icons-round">warning</span>Overdue (${fmt})</span>`;
    if (d === 0) return `<span class="due-badge due-today"><span class="material-icons-round">alarm</span>Due Today!</span>`;
    if (d === 1) return `<span class="due-badge due-tomorrow"><span class="material-icons-round">alarm</span>Tomorrow</span>`;
    if (d <= 3)  return `<span class="due-badge due-soon"><span class="material-icons-round">alarm</span>${d} days left</span>`;
    return `<span class="due-badge due-future"><span class="material-icons-round">calendar_today</span>${fmt}</span>`;
}

/* ════════════════════════════════════════════
   DIFFICULTY DOTS
════════════════════════════════════════════ */
function diffDots(n) {
    n = Math.max(1, Math.min(10, n|0));
    const cls = n <= 3 ? 'e' : n <= 6 ? 'm' : 'h';
    return `<div class="diff-dots" aria-label="Difficulty ${n}/10">`
        + Array.from({length:10}, (_,i) => `<div class="dd${i<n?' '+cls:''}"></div>`).join('')
        + '</div>';
}

/* ════════════════════════════════════════════
   HELPERS
════════════════════════════════════════════ */
function fmtMin(m) {
    if (!m) return 'TBD';
    const h = m/60|0, r = m%60;
    return h && r ? `${h}h ${r}m` : h ? `${h}h` : `${r}m`;
}

function clsIcon(c) {
    return {Assignment:'edit_note',CIE:'quiz',Practical:'science',Project:'rocket_launch',Other:'more_horiz',Task:'more_horiz'}[c]||'assignment';
}

function subHue(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = s.charCodeAt(i) + ((h<<5)-h);
    return Math.abs(h) % 360;
}

function hslColor(s) {
    const hue = subHue(s);
    const dark = document.body.dataset.theme === 'dark';
    return `hsl(${hue}, ${dark?'52%':'62%'}, ${dark?'56%':'44%'})`;
}

function subChipStyle(s) {
    const hue = subHue(s);
    const dark = document.body.dataset.theme === 'dark';
    const a = dark ? .15 : .1;
    return `background:hsla(${hue},55%,55%,${a});color:hsl(${hue},${dark?'52%':'62%'},${dark?'56%':'44%'});border:1px solid hsla(${hue},55%,55%,.25)`;
}

function subAbbr(s) {
    const w = s.replace(/[^A-Za-z0-9 ]/g,' ').trim().split(/\s+/);
    return w.length >= 2 ? (w[0][0]+w[1][0]).toUpperCase() : s.slice(0,2).toUpperCase();
}

/* ════════════════════════════════════════════
   SORT HELPER
════════════════════════════════════════════ */
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

function shortSub(s) {
    const m = s.match(/^([A-Z]{2,6}\d{3}[A-Z]?)/);
    if (m) return m[1];
    return s.length > 22 ? s.slice(0,20)+'…' : s;
}

function esc(s) {
    if (!s) return '';
    return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function empty(ico, title, desc) {
    return `<div class="empty"><span class="material-icons-round">${ico}</span><h3>${title}</h3><p>${desc}</p></div>`;
}

function id(i) { return document.getElementById(i); }
function setText(i, v) { const e = id(i); if(e) e.textContent = v; }
