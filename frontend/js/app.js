/* ClassFlow — App Core: Init, Navigation, Task Actions */
'use strict';

/* ════════════════════════════════════════════
   DATA
════════════════════════════════════════════ */
async function refreshAll(forceSync = false) {
    const ico = id('refresh-ico');
    if (ico) ico.style.animation = 'spinR .7s linear infinite';
    hideErr();
    try {
        if (forceSync) {
            // Trigger manual classroom sync on backend first
            await api('/tasks/sync', { method: 'POST' });
        }

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
        if (ico) ico.style.animation = '';
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
    if (v === 'reviews')   renderReviews();
    if (v === 'admin-feedback') renderAdminFeedback();
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
        if (S.view === 'subjects') {
            renderSubjects();
        } else if (S.search && S.view !== 'tasks') {
            const q = S.search.toLowerCase();
            const hasMatchingTasks = S.tasks.some(t =>
                !t.is_completed && (
                    t.title.toLowerCase().includes(q) ||
                    t.subject.toLowerCase().includes(q) ||
                    (t.summary||'').toLowerCase().includes(q)
                )
            );
            const hasMatchingSubjects = S.subjects.some(s =>
                s.subject.toLowerCase().includes(q)
            );
            
            if (!hasMatchingTasks && hasMatchingSubjects) {
                show('subjects');
            } else {
                show('tasks');
            }
        } else {
            renderTasks();
        }
    }, 200);
});

/* ════════════════════════════════════════════
   KEYBOARD SHORTCUTS
════════════════════════════════════════════ */
document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); id('search-inp').focus(); }
    if (e.key === 'Escape') { closeModal(); closeFeedbackModal(); }
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


/* ════════════════════════════════════════════
   FEEDBACK MODAL
   ════════════════════════════════════════════ */
let selectedRating = null;
let selectedCategory = null;

const RATING_CAPTIONS = {
    1: "Requires Significant Improvement",
    2: "Needs Improvement",
    3: "Meets Expectations",
    4: "Very Good / Exceeds Expectations",
    5: "Excellent / Outstanding Experience"
};

const CATEGORY_PLACEHOLDERS = {
    'Report Issue': "Please describe the technical issue or unexpected behavior you encountered...",
    'Suggest Enhancement': "Please describe your suggestion or feature request, and how it would improve your workflow...",
    'Share Praise': "Please tell us what you like about ClassFlow, or share your positive experience...",
    'General Inquiry': "Please share any other thoughts, comments, or general inquiries you have..."
};

window.openFeedbackModal = function() {
    selectedRating = null;
    selectedCategory = null;
    
    // Reset stars
    document.querySelectorAll('#feedback-modal-bg .star-btn').forEach(btn => {
        btn.classList.remove('active');
        const icon = btn.querySelector('.material-icons-round');
        if (icon) icon.textContent = 'star_outline';
    });
    id('rating-caption').textContent = 'Please select a rating';
    
    // Reset chips
    document.querySelectorAll('#feedback-modal-bg .chip-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    
    // Reset textarea
    const textarea = id('feedback-comment');
    textarea.value = '';
    textarea.placeholder = 'Please select a category above and share your thoughts...';
    
    // Reset form view & banner
    id('feedback-form-container').style.display = 'block';
    id('feedback-success-banner').classList.remove('open');
    
    // Reset submit button
    const submitBtn = id('feedback-submit-btn');
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<span class="material-icons-round" style="font-size:17px">send</span> Submit Feedback';
    
    // Open modal
    id('feedback-modal-bg').classList.add('open');
    document.body.style.overflow = 'hidden';
};

window.closeFeedbackModal = function() {
    id('feedback-modal-bg').classList.remove('open');
    document.body.style.overflow = '';
};

window.bgFeedbackClick = function(e) {
    if (e.target === id('feedback-modal-bg')) {
        closeFeedbackModal();
    }
};

window.selectRating = function(rating) {
    selectedRating = rating;
    document.querySelectorAll('#feedback-modal-bg .star-btn').forEach(btn => {
        const val = parseInt(btn.dataset.value, 10);
        const icon = btn.querySelector('.material-icons-round');
        if (val <= rating) {
            btn.classList.add('active');
            if (icon) icon.textContent = 'star';
        } else {
            btn.classList.remove('active');
            if (icon) icon.textContent = 'star_outline';
        }
    });
    id('rating-caption').textContent = RATING_CAPTIONS[rating] || 'Please select a rating';
};

window.selectCategory = function(category) {
    selectedCategory = category;
    document.querySelectorAll('#feedback-modal-bg .chip-btn').forEach(btn => {
        if (btn.dataset.category === category) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
    const textarea = id('feedback-comment');
    textarea.placeholder = CATEGORY_PLACEHOLDERS[category] || 'Please share your thoughts...';
    textarea.focus();
};

window.submitFeedback = async function(e) {
    e.preventDefault();
    
    const comment = id('feedback-comment').value.trim();
    
    if (selectedRating === null) {
        toast('Please select a rating star.', true);
        return;
    }
    if (!selectedCategory) {
        toast('Please select a feedback category.', true);
        return;
    }
    if (!comment) {
        toast('Please enter your comments.', true);
        return;
    }
    
    const submitBtn = id('feedback-submit-btn');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<div class="spin"></div> Submitting...';
    
    try {
        const payload = {
            rating: selectedRating,
            category: selectedCategory,
            comment: comment
        };
        
        await api('/feedback', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        
        // Switch to success view
        id('feedback-form-container').style.display = 'none';
        id('feedback-success-banner').classList.add('open');
        
        // Auto close after 3 seconds
        setTimeout(() => {
            closeFeedbackModal();
        }, 3000);
        
    } catch (err) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<span class="material-icons-round" style="font-size:17px">send</span> Submit Feedback';
        toast(err.message, true);
    }
};

/* ════════════════════════════════════════════
   FEEDBACK VIEW & MODERATION
   ════════════════════════════════════════════ */

window.renderReviews = async function() {
    const grid = id('reviews-grid');
    const countLbl = id('reviews-count');
    if (!grid) return;
    
    grid.innerHTML = `
        <div class="notif-empty">
            <div class="spin" style="border-top-color:var(--primary);width:24px;height:24px;"></div>
            <p>Loading testimonials...</p>
        </div>`;
    if (countLbl) countLbl.textContent = '';
    
    try {
        const reviews = await api('/feedback');
        if (countLbl) countLbl.textContent = `${reviews.length} Approved`;
        
        if (!reviews.length) {
            grid.innerHTML = `
                <div class="notif-empty">
                    <span class="material-icons-round">favorite_border</span>
                    <p>No testimonials have been published yet.</p>
                </div>`;
            return;
        }
        
        grid.innerHTML = reviews.map(r => {
            const initial = getCleanInitial(r.name || 'Anonymous', '');
            const imgHtml = r.picture 
                ? `<img class="review-avatar" src="${r.picture}" alt="${esc(r.name)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
                   <div class="review-avatar" style="display:none;align-items:center;justify-content:center;font-weight:600;font-size:0.875rem;background:var(--primary-container);color:var(--primary)">${esc(initial)}</div>`
                : `<div class="review-avatar" style="display:flex;align-items:center;justify-content:center;font-weight:600;font-size:0.875rem;background:var(--primary-container);color:var(--primary)">${esc(initial)}</div>`;
                
            let starsHtml = '';
            for (let i = 1; i <= 5; i++) {
                starsHtml += `<span class="material-icons-round">${i <= r.rating ? 'star' : 'star_outline'}</span>`;
            }
            
            const dateStr = r.created_at ? new Date(r.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '';
            
            return `
                <div class="review-card">
                    <div class="review-card-hdr">
                        ${imgHtml}
                        <div class="review-user-meta">
                            <span class="review-username">${esc(r.name || 'Anonymous')}</span>
                            <span class="review-date">${esc(dateStr)}</span>
                        </div>
                    </div>
                    <div class="review-rating-row">
                        <div class="review-stars">${starsHtml}</div>
                        <span class="review-category">${esc(r.category)}</span>
                    </div>
                    <p class="review-comment">${esc(r.comment)}</p>
                </div>
            `;
        }).join('');
    } catch (err) {
        grid.innerHTML = `
            <div class="notif-empty">
                <span class="material-icons-round" style="color:var(--error)">error_outline</span>
                <p>Failed to load reviews: ${esc(err.message)}</p>
            </div>`;
    }
};

window.renderAdminFeedback = async function() {
    const list = id('admin-feedback-list');
    const countLbl = id('admin-feedback-count');
    if (!list) return;
    
    list.innerHTML = `
        <div class="notif-empty">
            <div class="spin" style="border-top-color:var(--primary);width:24px;height:24px;"></div>
            <p>Loading system feedback submissions...</p>
        </div>`;
    if (countLbl) countLbl.textContent = '';
    
    try {
        const feedbacks = await api('/admin/feedback');
        if (countLbl) countLbl.textContent = `${feedbacks.length} Total`;
        
        if (!feedbacks.length) {
            list.innerHTML = `
                <div class="notif-empty">
                    <span class="material-icons-round">admin_panel_settings</span>
                    <p>No feedback submissions found in the database.</p>
                </div>`;
            return;
        }
        
        list.innerHTML = feedbacks.map(f => {
            const initial = getCleanInitial(f.name || 'Anonymous', f.email || '');
            const imgHtml = f.picture 
                ? `<img class="review-avatar" src="${f.picture}" alt="${esc(f.name)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
                   <div class="review-avatar" style="display:none;align-items:center;justify-content:center;font-weight:600;font-size:0.875rem;background:var(--primary-container);color:var(--primary)">${esc(initial)}</div>`
                : `<div class="review-avatar" style="display:flex;align-items:center;justify-content:center;font-weight:600;font-size:0.875rem;background:var(--primary-container);color:var(--primary)">${esc(initial)}</div>`;
                
            let starsHtml = '';
            for (let i = 1; i <= 5; i++) {
                starsHtml += `<span class="material-icons-round" style="font-size:16px">${i <= f.rating ? 'star' : 'star_outline'}</span>`;
            }
            
            const dateStr = f.created_at ? new Date(f.created_at).toLocaleString() : '';
            const checked = f.approved ? 'checked' : '';
            const statusClass = f.approved ? 'active' : '';
            const statusText = f.approved ? 'Approved' : 'Pending';
            
            return `
                <div class="feedback-admin-row" id="far-${f.id}">
                    <div class="feedback-admin-user">
                        ${imgHtml}
                        <div class="feedback-admin-details">
                            <span class="review-username">${esc(f.name || 'Anonymous')}</span>
                            <span class="feedback-admin-email">${esc(f.email || '')}</span>
                        </div>
                    </div>
                    <div class="feedback-admin-content">
                        <div class="feedback-admin-meta">
                            <div class="review-stars">${starsHtml}</div>
                            <span class="review-category">${esc(f.category)}</span>
                            <span class="review-date">${esc(dateStr)}</span>
                        </div>
                        <p class="review-comment" style="font-size:0.8125rem;">${esc(f.comment)}</p>
                    </div>
                    <div class="feedback-admin-actions">
                        <span class="feedback-admin-status-lbl ${statusClass}" id="fasl-${f.id}">${statusText}</span>
                        <label class="switch-toggle" aria-label="Approve testimonial">
                            <input type="checkbox" ${checked} onchange="toggleFeedbackApproval(${f.id}, this.checked)">
                            <span class="switch-slider"></span>
                        </label>
                    </div>
                </div>
            `;
        }).join('');
    } catch (err) {
        list.innerHTML = `
            <div class="notif-empty">
                <span class="material-icons-round" style="color:var(--error)">error_outline</span>
                <p>Failed to load administrative list: ${esc(err.message)}</p>
            </div>`;
    }
};

window.toggleFeedbackApproval = async function(fid, isApproved) {
    const statusLbl = id(`fasl-${fid}`);
    if (statusLbl) {
        statusLbl.textContent = isApproved ? 'Approved' : 'Pending';
        statusLbl.classList.toggle('active', isApproved);
    }
    
    try {
        await api(`/admin/feedback/${fid}/approve`, {
            method: 'POST',
            body: JSON.stringify({ approved: isApproved })
        });
        toast(isApproved ? 'Review approved for Wall of Love' : 'Review removed from Wall of Love');
    } catch (err) {
        toast(err.message, true);
        renderAdminFeedback();
    }
};

(async function init() {
    initTheme();
    
    // Check session authentication status first
    const loggedIn = await checkAuth();
    if (!loggedIn) {
        return; // Bypasses data loading if not logged in
    }

    initInstallPrompt();
    if (window.checkPwaPushPrompt) {
        window.checkPwaPushPrompt();
    }
    await refreshAll();
    if (window.checkPushSubscriptionState) {
        await window.checkPushSubscriptionState();
    }
})();
