/* ==========================================================================
   DeepAgent Lab - frontend
   No third-party scripts. Every piece of server or model text is inserted
   with textContent / DOM nodes, never innerHTML. The session lives in
   HttpOnly cookies, so this script never sees a token (except the one-time
   tokens in an email link, which it hands to the server and discards).
   ========================================================================== */

(function () {
    'use strict';

    // ----------------------------------------------------------------------
    // Pipeline definition (single source for the explainer and the rail)
    // ----------------------------------------------------------------------
    const AGENTS = [
        { id: 'planner', name: 'Planner', role: 'Splits the question into focused sub-questions.' },
        { id: 'researcher', name: 'Researchers', role: 'One per sub-question, searching in parallel.' },
        { id: 'collector', name: 'Collector', role: 'Merges the findings and removes duplicate sources.' },
        { id: 'analyst', name: 'Analyst', role: 'Cross-references sources and rates the evidence.' },
        { id: 'critic', name: 'Critic', role: 'Checks claims against sources; can order a replan.' },
        { id: 'writer', name: 'Writer', role: 'Drafts the report with inline citations.' },
        { id: 'editor', name: 'Editor', role: 'QA gate: citations, completeness, structure.' },
    ];
    const AGENT_NAME = Object.fromEntries(AGENTS.map(a => [a.id, a.name]));
    const ACTIVE = ['queued', 'running', 'awaiting_approval'];
    const STATUS_LABEL = {
        queued: 'Queued', running: 'Running', awaiting_approval: 'Needs approval',
        completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled',
    };
    const DEFAULT_TITLE = 'DeepAgent Lab - Multi-Agent Research';

    const $ = id => document.getElementById(id);
    const SVG_NS = 'http://www.w3.org/2000/svg';

    // ----------------------------------------------------------------------
    // DOM helpers
    // ----------------------------------------------------------------------
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function icon(name, className) {
        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('class', className || 'icon');
        svg.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS(SVG_NS, 'use');
        use.setAttribute('href', `#i-${name}`);
        svg.appendChild(use);
        return svg;
    }

    function button(label, className, onClick) {
        const node = el('button', className || 'btn btn-ghost btn-sm', label);
        node.type = 'button';
        node.addEventListener('click', onClick);
        return node;
    }

    function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    const capitalize = text => String(text).charAt(0).toUpperCase() + String(text).slice(1);
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    const percent = v => `${Math.round((Number(v) || 0) * 100)}%`;

    function formatDuration(ms) {
        if (!Number.isFinite(ms) || ms < 0) return '';
        if (ms < 1000) return `${Math.round(ms)}ms`;
        const seconds = ms / 1000;
        if (seconds < 60) return `${seconds.toFixed(1)}s`;
        return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    }

    function formatTimeAgo(iso) {
        const then = new Date(iso);
        if (Number.isNaN(then.getTime())) return '';
        const minutes = Math.floor((Date.now() - then.getTime()) / 60000);
        if (minutes < 1) return 'just now';
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        return days < 7 ? `${days}d ago` : then.toLocaleDateString();
    }

    function formatDate(iso) {
        const date = iso ? new Date(iso) : new Date();
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    }

    /** Only http(s) links are ever made clickable. */
    function safeUrl(url) {
        try {
            const parsed = new URL(String(url));
            return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null;
        } catch (e) {
            return null;
        }
    }

    function externalLink(url, label, className) {
        const href = safeUrl(url);
        if (!href) return el('span', className, label || String(url || ''));
        const a = el('a', className, label || href);
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        return a;
    }

    function meter(value, label) {
        const v = Math.max(0, Math.min(1, Number(value) || 0));
        const wrap = el('div', 'meter');
        wrap.dataset.tone = v >= 0.7 ? 'good' : v >= 0.4 ? 'warn' : 'bad';
        wrap.appendChild(el('span', 'meter-label', label));
        const track = el('span', 'meter-track');
        const fill = el('span', 'meter-fill');
        track.appendChild(fill);
        wrap.appendChild(track);
        wrap.appendChild(el('span', 'meter-value', percent(v)));
        requestAnimationFrame(() => fill.style.setProperty('--v', String(v)));
        return wrap;
    }

    function tagList(items, className) {
        const list = el('ul', className || 'tags');
        items.forEach(item => list.appendChild(el('li', null, item)));
        return list;
    }

    // ----------------------------------------------------------------------
    // Safe, minimal Markdown -> DOM
    // ----------------------------------------------------------------------
    const INLINE = /(\*\*([^*]+)\*\*|\*([^*\n]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\[(\d{1,3})\])/g;

    function renderInline(text, target) {
        let last = 0;
        let match;
        INLINE.lastIndex = 0;
        while ((match = INLINE.exec(text)) !== null) {
            if (match.index > last) target.appendChild(document.createTextNode(text.slice(last, match.index)));
            if (match[2] !== undefined) target.appendChild(el('strong', null, match[2]));
            else if (match[3] !== undefined) target.appendChild(el('em', null, match[3]));
            else if (match[4] !== undefined) target.appendChild(el('code', null, match[4]));
            else if (match[5] !== undefined) target.appendChild(externalLink(match[6], match[5]));
            else if (match[7] !== undefined) {
                const cite = el('a', 'cite', match[7]);
                cite.href = `#source-${match[7]}`;
                cite.setAttribute('aria-label', `Source ${match[7]}`);
                target.appendChild(cite);
            }
            last = INLINE.lastIndex;
        }
        if (last < text.length) target.appendChild(document.createTextNode(text.slice(last)));
        return target;
    }

    function renderMarkdown(text) {
        const root = el('div', 'prose');
        let list = null;
        let paragraph = [];
        const flushParagraph = () => {
            if (paragraph.length) {
                root.appendChild(renderInline(paragraph.join(' '), el('p')));
                paragraph = [];
            }
        };
        String(text || '').split(/\r?\n/).forEach(rawLine => {
            const line = rawLine.trim();
            const heading = /^(#{1,4})\s+(.*)$/.exec(line);
            const bullet = /^[-*•]\s+(.*)$/.exec(line);
            const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
            if (!line) { flushParagraph(); list = null; return; }
            if (heading) {
                flushParagraph(); list = null;
                root.appendChild(renderInline(heading[2], el(heading[1].length <= 3 ? 'h3' : 'h4')));
                return;
            }
            if (bullet || numbered) {
                flushParagraph();
                const tag = bullet ? 'ul' : 'ol';
                if (!list || list.tagName.toLowerCase() !== tag) {
                    list = el(tag);
                    root.appendChild(list);
                }
                list.appendChild(renderInline((bullet || numbered)[1], el('li')));
                return;
            }
            list = null;
            paragraph.push(line);
        });
        flushParagraph();
        return root;
    }

    // ----------------------------------------------------------------------
    // State
    // ----------------------------------------------------------------------
    const views = {
        dashboard: $('view-dashboard'), new: $('view-new'), run: $('view-run'),
        report: $('view-report'), users: $('view-users'), error: $('view-error'),
    };
    const input = $('query-input');
    const rail = $('rail');
    const logList = $('log');
    const loops = $('loops');
    const sidebar = $('sidebar');
    const toast = $('toast');
    const confirmDialog = $('confirm-dialog');

    let user = null;              // signed-in user, or null
    let maxQueryLength = 500;
    let runs = [];                // dashboard cache
    let runFilter = 'all';
    let watch = null;             // the run currently open in the run view
    let currentReport = null;     // { run } shown in the report view
    let resetToken = null;        // one-time token from a password-recovery link
    let pollTimer = null;
    let clockTimer = null;
    let toastTimer = null;
    let confirmAction = null;

    // ----------------------------------------------------------------------
    // API
    // ----------------------------------------------------------------------
    class ApiError extends Error {
        constructor(status, message) { super(message); this.status = status; }
    }

    async function errorMessage(response) {
        let detail = '';
        try {
            const body = await response.json();
            if (typeof body.detail === 'string') detail = body.detail;
            else if (Array.isArray(body.detail) && body.detail[0]) {
                detail = String(body.detail[0].msg || '').replace(/^Value error, /, '');
            }
        } catch (e) { /* not JSON */ }
        return detail || `The server returned an error (HTTP ${response.status}).`;
    }

    async function api(path, options) {
        const init = { credentials: 'same-origin', ...(options || {}) };
        if (init.body !== undefined && typeof init.body !== 'string') {
            init.body = JSON.stringify(init.body);
            init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
        }
        let response;
        try {
            response = await fetch(path, init);
        } catch (e) {
            if (e.name === 'AbortError') throw e;
            throw new ApiError(0, 'The server is unreachable. Check your connection.');
        }
        if (response.status === 401 && user && !path.startsWith('/auth/')) {
            signedOut('Your session has ended. Sign in again.');
            throw new ApiError(401, 'Session ended.');
        }
        if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
        return init.raw ? response : response.json();
    }

    function showToast(message) {
        toast.textContent = message;
        toast.classList.add('is-visible');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 3200);
    }

    function confirmDanger(title, text, okLabel, action) {
        $('confirm-title').textContent = title;
        $('confirm-text').textContent = text;
        $('confirm-ok').textContent = okLabel;
        confirmAction = action;
        confirmDialog.showModal();
    }

    // ----------------------------------------------------------------------
    // Authentication screen
    // ----------------------------------------------------------------------
    const AUTH_MODES = {
        login: { form: 'login-form', title: 'Sign in', lede: 'Research with seven agents and a cited report at the end.' },
        register: { form: 'register-form', title: 'Create your account', lede: 'Your research and reports are saved to your account.' },
        forgot: { form: 'forgot-form', title: 'Reset your password', lede: 'Enter your email and we will send a reset link.' },
        reset: { form: 'reset-form', title: 'Choose a new password', lede: 'Set a new password for your account.' },
    };

    function setAuthMode(mode, notice) {
        Object.entries(AUTH_MODES).forEach(([name, config]) => { $(config.form).hidden = name !== mode; });
        $('auth-title').textContent = AUTH_MODES[mode].title;
        $('auth-lede').textContent = AUTH_MODES[mode].lede;
        setAuthMessage('auth-error', '');
        setAuthMessage('auth-notice', notice || '');
        const first = $(AUTH_MODES[mode].form).querySelector('input');
        if (first) first.focus();
    }

    function setAuthMessage(id, message) {
        $(id).textContent = message;
        $(id).hidden = !message;
    }

    function signedOut(notice) {
        user = null;
        stopWatching();
        clearInterval(pollTimer);
        pollTimer = null;
        runs = [];
        currentReport = null;
        $('app').hidden = true;
        $('boot').hidden = true;
        $('auth').hidden = false;
        document.title = DEFAULT_TITLE;
        setAuthMode(resetToken ? 'reset' : 'login', notice);
    }

    function signedIn(account) {
        user = account;
        resetToken = null;
        $('auth').hidden = true;
        $('boot').hidden = true;
        $('app').hidden = false;
        $('account-name').textContent = account.full_name || account.email;
        $('account-email').textContent = account.full_name ? account.email : '';
        $('account-role').hidden = account.role !== 'admin';
        $('nav-users').hidden = account.role !== 'admin';
        document.querySelectorAll('.auth-form').forEach(form => form.reset());
        checkHealth();
        if (!location.hash.startsWith('#/')) location.hash = '#/dashboard';
        else route();
        startPolling();
    }

    async function submitAuth(form, handler) {
        const submit = form.querySelector('button[type="submit"]');
        setAuthMessage('auth-error', '');
        if (!form.checkValidity()) {
            const invalid = form.querySelector(':invalid');
            setAuthMessage('auth-error', invalid.validity.valueMissing
                ? 'Fill in every field.'
                : invalid.type === 'email' ? 'Enter a valid email address.'
                    : 'The password must be at least 8 characters.');
            invalid.focus();
            return;
        }
        const password = form.querySelector('input[autocomplete="new-password"]');
        if (password && password.value.length < 8) {
            setAuthMessage('auth-error', 'The password must be at least 8 characters.');
            password.focus();
            return;
        }
        submit.disabled = true;
        try {
            await handler();
        } catch (error) {
            setAuthMessage('auth-notice', '');
            setAuthMessage('auth-error', error.message);
        } finally {
            submit.disabled = false;
        }
    }

    function wireAuth() {
        document.querySelectorAll('[data-auth-mode]').forEach(link => {
            link.addEventListener('click', () => setAuthMode(link.dataset.authMode));
        });

        $('login-form').addEventListener('submit', event => {
            event.preventDefault();
            submitAuth(event.target, async () => {
                const body = await api('/auth/login', { method: 'POST', body: {
                    email: $('login-email').value.trim(), password: $('login-password').value,
                } });
                signedIn(body.user);
            });
        });

        $('register-form').addEventListener('submit', event => {
            event.preventDefault();
            submitAuth(event.target, async () => {
                const email = $('register-email').value.trim();
                const body = await api('/auth/register', { method: 'POST', body: {
                    full_name: $('register-name').value.trim(), email,
                    password: $('register-password').value,
                } });
                if (body.status === 'signed_in') { signedIn(body.user); return; }
                setAuthMode('login', `We sent a confirmation link to ${email}. Open it, then sign in.`);
                $('login-email').value = email;
                $('login-password').focus();
            });
        });

        $('forgot-form').addEventListener('submit', event => {
            event.preventDefault();
            submitAuth(event.target, async () => {
                const body = await api('/auth/forgot', { method: 'POST',
                    body: { email: $('forgot-email').value.trim() } });
                setAuthMode('login', body.message);
            });
        });

        $('reset-form').addEventListener('submit', event => {
            event.preventDefault();
            submitAuth(event.target, async () => {
                const body = await api('/auth/reset', { method: 'POST', body: {
                    access_token: resetToken, password: $('reset-password').value,
                } });
                resetToken = null;
                setAuthMode('login', body.message);
            });
        });

        $('logout-btn').addEventListener('click', async () => {
            try { await api('/auth/logout', { method: 'POST' }); } catch (e) { /* sign out locally anyway */ }
            location.hash = '';
            signedOut('You have signed out.');
        });
    }

    /**
     * Supabase email links return to this page with tokens in the URL fragment.
     * Hand them to the server (which sets the session cookies) and remove them
     * from the address bar and history immediately.
     */
    async function consumeEmailLink() {
        const fragment = location.hash.slice(1);
        if (!fragment.includes('access_token=') && !fragment.includes('error_description=')) return null;
        const params = new URLSearchParams(fragment);
        history.replaceState(null, '', location.pathname);

        if (params.get('error_description')) {
            return { notice: '', error: params.get('error_description') };
        }
        if (params.get('type') === 'recovery') {
            resetToken = params.get('access_token');
            return { notice: '' };
        }
        try {
            const body = await api('/auth/session', { method: 'POST', body: {
                access_token: params.get('access_token'), refresh_token: params.get('refresh_token') || '',
            } });
            return { user: body.user };
        } catch (error) {
            return { error: error.message };
        }
    }

    // ----------------------------------------------------------------------
    // Routing
    // ----------------------------------------------------------------------
    function showView(name, title) {
        Object.entries(views).forEach(([key, node]) => { node.hidden = key !== name; });
        const view = views[name];
        view.classList.remove('view-enter');
        void view.offsetWidth; // restart the entrance animation
        view.classList.add('view-enter');
        $('topbar-title').textContent = title;
        document.querySelectorAll('.nav-link').forEach(link => {
            const active = link.dataset.nav === name;
            link.classList.toggle('is-active', active);
            if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
        });
        window.scrollTo(0, 0);
    }

    function showError(message) {
        $('error-message').textContent = message;
        showView('error', 'Error');
    }

    function route() {
        if (!user) return;
        const [, section, id] = location.hash.split('/');
        closeSidebar();
        setDownloadMenu(false);
        if (section !== 'runs') stopWatching();
        document.title = DEFAULT_TITLE;

        if (section === 'new') {
            showView('new', 'New research');
            input.focus();
        } else if (section === 'runs' && id) {
            openRun(id);
        } else if (section === 'users' && user.role === 'admin') {
            showView('users', 'Users');
            loadUsers();
        } else {
            if (section !== 'dashboard') history.replaceState(null, '', '#/dashboard');
            showView('dashboard', 'Dashboard');
            refreshRuns();
        }
    }

    // ----------------------------------------------------------------------
    // Dashboard
    // ----------------------------------------------------------------------
    function stageLabel(run) {
        if (run.status === 'queued') return 'Waiting for a free slot';
        if (run.status === 'awaiting_approval') return 'Waiting for your approval';
        return run.stage ? `${AGENT_NAME[run.stage] || capitalize(run.stage)} working` : 'Working';
    }

    function runDuration(run) {
        if (!run.started_at) return '';
        const end = run.finished_at ? new Date(run.finished_at) : new Date();
        return formatDuration(end - new Date(run.started_at));
    }

    async function refreshRuns() {
        let body;
        try {
            body = await api('/runs');
        } catch (error) {
            if (error.status !== 401) showToast(error.message);
            return;
        }
        runs = body.runs || [];
        renderDashboard();
    }

    function startPolling() {
        clearInterval(pollTimer);
        // Active runs change server-side; poll while any exist, and lightly otherwise.
        let tick = 0;
        pollTimer = setInterval(() => {
            if (!user || document.hidden) return;
            tick += 1;
            const anyActive = runs.some(run => ACTIVE.includes(run.status));
            if (anyActive || tick % 8 === 0) refreshRuns();
        }, 2500);
    }

    function renderDashboard() {
        const active = runs.filter(run => ACTIVE.includes(run.status));
        const completed = runs.filter(run => run.status === 'completed');
        const stopped = runs.filter(run => run.status === 'failed' || run.status === 'cancelled');
        $('tile-total').textContent = String(runs.length);
        $('tile-active').textContent = String(active.length);
        $('tile-completed').textContent = String(completed.length);
        $('tile-stopped').textContent = String(stopped.length);
        $('nav-active').hidden = active.length === 0;
        $('nav-active').textContent = String(active.length);
        if (views.dashboard.hidden) return;

        // In progress
        $('active-section').hidden = active.length === 0;
        const activeList = $('active-list');
        const seen = new Set();
        active.forEach(run => {
            seen.add(run.id);
            let card = activeList.querySelector(`[data-run-id="${run.id}"]`);
            if (!card) {
                card = buildActiveCard(run);
                activeList.appendChild(card);
            }
            card.dataset.status = run.status;
            card.querySelector('.active-stage').textContent = stageLabel(run);
            card.querySelector('.active-meta').textContent =
                [run.round > 1 ? `Round ${run.round}` : '', runDuration(run)].filter(Boolean).join(' · ');
            card.querySelector('.progress-fill').style.setProperty('--v', String((run.progress || 0) / 100));
            card.querySelector('.progress').setAttribute('aria-valuenow', String(run.progress || 0));
        });
        Array.from(activeList.children).forEach(card => {
            if (!seen.has(card.dataset.runId)) card.remove();
        });

        // All research
        const term = $('run-search').value.trim().toLowerCase();
        const visible = runs.filter(run => {
            if (runFilter === 'completed' && run.status !== 'completed') return false;
            if (runFilter === 'stopped' && run.status !== 'failed' && run.status !== 'cancelled') return false;
            return !term || `${run.query} ${run.title}`.toLowerCase().includes(term);
        });
        const list = $('run-list');
        clear(list);
        visible.forEach(run => list.appendChild(buildRunRow(run)));
        const empty = $('run-empty');
        empty.hidden = visible.length > 0;
        empty.textContent = runs.length === 0
            ? 'No research yet. Start your first run and it will appear here.'
            : 'No research matches this filter.';
    }

    function buildActiveCard(run) {
        const card = el('li', 'active-card');
        card.dataset.runId = run.id;
        const link = el('a', 'active-query', run.query);
        link.href = `#/runs/${run.id}`;
        card.appendChild(link);

        const line = el('div', 'active-line');
        line.appendChild(el('span', 'active-stage'));
        line.appendChild(el('span', 'active-meta'));
        card.appendChild(line);

        const progress = el('div', 'progress');
        progress.setAttribute('role', 'progressbar');
        progress.setAttribute('aria-valuemin', '0');
        progress.setAttribute('aria-valuemax', '100');
        progress.setAttribute('aria-label', 'Run progress');
        progress.appendChild(el('span', 'progress-fill'));
        card.appendChild(progress);

        const actions = el('div', 'row-actions');
        const open = el('a', 'btn btn-ghost btn-sm', 'Watch');
        open.href = `#/runs/${run.id}`;
        actions.appendChild(open);
        actions.appendChild(button('Cancel', 'btn btn-ghost btn-sm', () => cancelRun(run.id)));
        card.appendChild(actions);
        return card;
    }

    function buildRunRow(run) {
        const row = el('li', 'run-row');
        row.dataset.status = run.status;

        const main = el('div', 'run-main');
        const link = el('a', 'run-title', run.title || run.query);
        link.href = `#/runs/${run.id}`;
        main.appendChild(link);
        if (run.title) main.appendChild(el('span', 'run-sub', run.query));
        else if (run.error && !ACTIVE.includes(run.status)) main.appendChild(el('span', 'run-sub', run.error));
        row.appendChild(main);

        const meta = el('div', 'run-meta');
        meta.appendChild(el('span', 'status-chip', STATUS_LABEL[run.status] || run.status));
        const facts = [formatTimeAgo(run.created_at)];
        if (run.status === 'completed') facts.push(plural(run.source_count, 'source'));
        if (run.finished_at) facts.push(runDuration(run));
        meta.appendChild(el('span', 'run-facts', facts.filter(Boolean).join(' · ')));
        row.appendChild(meta);

        const actions = el('div', 'row-actions');
        if (run.status === 'failed' || run.status === 'cancelled') {
            actions.appendChild(button('Run again', 'btn btn-ghost btn-sm', () => retryRun(run.id)));
        }
        if (ACTIVE.includes(run.status)) {
            actions.appendChild(button('Cancel', 'btn btn-ghost btn-sm', () => cancelRun(run.id)));
        } else {
            const remove = button('', 'icon-btn', () => askDeleteRun(run));
            remove.setAttribute('aria-label', `Delete: ${run.title || run.query}`);
            remove.appendChild(icon('trash'));
            actions.appendChild(remove);
        }
        row.appendChild(actions);
        return row;
    }

    async function cancelRun(runId) {
        try {
            await api(`/runs/${runId}/cancel`, { method: 'POST' });
            showToast('Cancelling the run');
        } catch (error) {
            showToast(error.message);
        }
        refreshRuns();
    }

    async function retryRun(runId) {
        try {
            const body = await api(`/runs/${runId}/retry`, { method: 'POST' });
            location.hash = `#/runs/${body.run.id}`;
        } catch (error) {
            showToast(error.message);
        }
    }

    function askDeleteRun(run) {
        confirmDanger('Delete this research?',
            'The run and its report are removed from your account and cannot be recovered.',
            'Delete', async () => {
                try {
                    await api(`/runs/${run.id}`, { method: 'DELETE' });
                    showToast('Research deleted');
                    if (location.hash.includes(run.id)) location.hash = '#/dashboard';
                    else refreshRuns();
                } catch (error) {
                    showToast(`Delete failed: ${error.message}`);
                }
            });
    }

    // ----------------------------------------------------------------------
    // New research
    // ----------------------------------------------------------------------
    function buildExplainer() {
        const list = $('how-list');
        AGENTS.forEach(agent => {
            const item = el('li');
            item.appendChild(el('strong', null, agent.name));
            item.appendChild(el('span', null, agent.role));
            list.appendChild(item);
        });
    }

    function updateCounter() {
        const length = input.value.length;
        const counter = $('query-counter');
        counter.textContent = `${length} / ${maxQueryLength}`;
        counter.classList.toggle('is-near', length > maxQueryLength * 0.9);
        input.style.height = '';
        if (length && input.scrollHeight > input.clientHeight) {
            input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
        }
    }

    function setFieldError(message) {
        $('query-error').textContent = message;
        $('query-error').hidden = !message;
        input.setAttribute('aria-invalid', message ? 'true' : 'false');
    }

    async function submitQuery() {
        const query = input.value.replace(/\s+/g, ' ').trim();
        if (query.length < 3) { setFieldError('Enter a question of at least 3 characters.'); input.focus(); return; }
        if (query.length > maxQueryLength) {
            setFieldError(`Keep the question under ${maxQueryLength} characters.`);
            input.focus();
            return;
        }
        setFieldError('');
        const submit = $('submit-btn');
        submit.disabled = true;
        try {
            const body = await api('/runs', { method: 'POST', body: { query } });
            input.value = '';
            updateCounter();
            location.hash = `#/runs/${body.run.id}`;
        } catch (error) {
            if (error.status !== 401) setFieldError(error.message);
        } finally {
            submit.disabled = false;
        }
    }

    // ----------------------------------------------------------------------
    // Run view: rail
    // ----------------------------------------------------------------------
    function buildRail() {
        clear(rail);
        AGENTS.forEach((agent, index) => {
            const stage = el('li', 'stage');
            stage.dataset.agent = agent.id;
            stage.dataset.state = 'waiting';

            const marker = el('div', 'stage-marker');
            marker.appendChild(el('span', 'stage-num', String(index + 1)));
            marker.appendChild(icon('check', 'icon stage-check'));
            marker.appendChild(el('span', 'stage-ring'));
            stage.appendChild(marker);

            const main = el('div', 'stage-main');
            const head = el('div', 'stage-head');
            head.appendChild(el('h3', null, agent.name));
            head.appendChild(el('span', 'stage-status', 'Waiting'));
            head.appendChild(el('span', 'stage-time'));
            main.appendChild(head);
            main.appendChild(el('p', 'stage-role', agent.role));
            main.appendChild(el('div', 'stage-body'));
            stage.appendChild(main);
            rail.appendChild(stage);
        });
    }

    const stageOf = agent => rail.querySelector(`.stage[data-agent="${agent}"]`);
    const STATUS_TEXT = { waiting: 'Waiting', active: 'Working', done: 'Done', warn: 'Sent back', stopped: 'Stopped' };

    function setStage(agent, state, durationMs) {
        const stage = stageOf(agent);
        if (!stage) return;
        stage.dataset.state = state;
        stage.querySelector('.stage-status').textContent = STATUS_TEXT[state] || state;
        const time = stage.querySelector('.stage-time');
        if (state === 'waiting') time.textContent = '';
        if (durationMs !== undefined) time.textContent = formatDuration(durationMs);
        if (state === 'active') {
            stage.dataset.startedAt = String(performance.now());
            if (watch && watch.live) stage.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        } else {
            delete stage.dataset.startedAt;
        }
    }

    function setStageBody(agent, ...children) {
        const stage = stageOf(agent);
        if (!stage) return;
        const body = stage.querySelector('.stage-body');
        clear(body);
        const inner = el('div', 'stage-detail');
        children.filter(Boolean).forEach(child => inner.appendChild(child));
        body.appendChild(inner);
    }

    function resetStages(agents) {
        agents.forEach(agent => {
            setStage(agent, 'waiting');
            const stage = stageOf(agent);
            if (stage) clear(stage.querySelector('.stage-body'));
        });
    }

    function log(source, message, tone) {
        const item = el('li', 'log-item');
        if (tone) item.dataset.tone = tone;
        item.appendChild(el('time', 'log-time', formatDuration(elapsedMs()) || '0.0s'));
        const text = el('span', 'log-text');
        text.appendChild(el('strong', null, source));
        text.appendChild(document.createTextNode(` ${message}`));
        item.appendChild(text);
        logList.appendChild(item);
        logList.scrollTop = logList.scrollHeight;
    }

    function addLoop(label, detail) {
        const pill = el('div', 'loop');
        pill.appendChild(icon('loop'));
        pill.appendChild(el('strong', null, label));
        if (detail) pill.appendChild(el('span', null, detail));
        loops.appendChild(pill);
    }

    function elapsedMs() {
        if (!watch || !watch.startedAt) return 0;
        return (watch.endedAt || Date.now()) - watch.startedAt;
    }

    function tickClock() {
        if (!watch) return;
        $('run-clock').textContent = formatDuration(elapsedMs()) || '0.0s';
        const now = performance.now();
        rail.querySelectorAll('.stage[data-state="active"]').forEach(stage => {
            const startedAt = Number(stage.dataset.startedAt);
            if (startedAt) stage.querySelector('.stage-time').textContent = formatDuration(now - startedAt);
        });
    }

    // ----------------------------------------------------------------------
    // Run view: open, subscribe, finish
    // ----------------------------------------------------------------------
    function stopWatching() {
        if (watch) watch.controller.abort();
        watch = null;
        clearInterval(clockTimer);
        clockTimer = null;
    }

    async function openRun(runId) {
        stopWatching();
        let run;
        try {
            run = (await api(`/runs/${runId}`)).run;
        } catch (error) {
            if (error.status !== 401) {
                showError(error.status === 404 || error.status === 422
                    ? 'That research run does not exist or belongs to another account.' : error.message);
            }
            return;
        }
        if (location.hash !== `#/runs/${runId}`) return; // navigated away while loading

        if (run.status === 'completed' && run.report) {
            showReport(run);
            return;
        }

        watch = {
            run,
            controller: new AbortController(),
            startedAt: run.started_at ? new Date(run.started_at).getTime() : Date.now(),
            endedAt: null,
            live: false,        // false while replaying history, true once caught up
            lanes: new Map(),
            lanesDone: 0,
            round: 1,
            finished: false,
        };
        buildRail();
        clear(logList);
        clear(loops);
        $('approval').hidden = true;
        $('stopped').hidden = true;
        $('run-query').textContent = run.query;
        $('run-state').textContent = STATUS_LABEL[run.status] || 'Researching';
        $('run-note').hidden = false;
        $('stat-round').textContent = '1';
        $('stat-subq').textContent = '-';
        $('stat-sources').textContent = '-';
        $('cancel-btn').hidden = false;
        $('cancel-btn').disabled = false;
        showView('run', 'Research run');
        clockTimer = setInterval(tickClock, 100);
        tickClock();

        follow(watch).catch(error => {
            if (error.name === 'AbortError' || watch === null) return;
            showStopped('Connection lost', `${error.message} The run is still going on the server; reopen it from the dashboard.`, false);
        });
        // Anything that arrives after the first burst is live, not history.
        const current = watch;
        setTimeout(() => { if (watch === current) current.live = true; }, 600);
    }

    async function follow(current) {
        const response = await api(`/runs/${current.run.id}/events`, {
            signal: current.controller.signal, raw: true,
        });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
            const frames = buffer.split('\n\n');
            buffer = frames.pop();
            frames.forEach(frame => dispatchFrame(frame, current));
        }
        if (watch === current && !current.finished) {
            // The stream closed without a terminal event: re-read the durable state.
            openRun(current.run.id);
        }
    }

    function dispatchFrame(frame, current) {
        if (watch !== current) return;
        let name = '';
        const dataLines = [];
        frame.split('\n').forEach(line => {
            if (line.startsWith('event:')) name = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
        });
        if (!name || !dataLines.length) return; // keepalive comment
        let data;
        try { data = JSON.parse(dataLines.join('\n')); } catch (e) { return; }
        if (handlers[name]) handlers[name](data);
    }

    function showStopped(title, message, canRetry) {
        if (watch) {
            watch.finished = true;
            watch.endedAt = Date.now();
        }
        clearInterval(clockTimer);
        rail.querySelectorAll('.stage[data-state="active"]').forEach(stage => {
            setStage(stage.dataset.agent, 'stopped');
        });
        rail.querySelectorAll('.lane[data-state="active"]').forEach(lane => { lane.dataset.state = 'empty'; });
        $('stopped-title').textContent = title;
        $('stopped-message').textContent = message;
        $('retry-btn').hidden = !canRetry;
        $('stopped-delete-btn').hidden = !canRetry;
        $('stopped').hidden = false;
        $('approval').hidden = true;
        $('cancel-btn').hidden = true;
        $('run-note').hidden = true;
        refreshRuns();
    }

    const handlers = {
        queued() {
            $('run-state').textContent = 'Queued';
            log('System', 'Queued. The run starts when a slot is free.');
        },

        research_start(data) {
            if (!watch.run.started_at) watch.startedAt = Date.now();
            $('run-state').textContent = 'Researching';
            log('System', 'Run started.');
            if (data.mock_mode) log('System', 'Mock mode: agents return canned output, no API calls.', 'warn');
        },

        agent_start(data) {
            if (data.agent === 'researcher') {
                if (watch.lanes.size === watch.lanesDone) startFanOut();
                addLane(data.task_id, data.sub_question);
                if (stageOf('researcher').dataset.state !== 'active') setStage('researcher', 'active');
                return;
            }
            setStage(data.agent, 'active');
            log(AGENT_NAME[data.agent], 'started.');
        },

        agent_complete(data) {
            if (data.agent === 'researcher') { finishLane(data.task_id, data.duration_ms); return; }
            const stage = stageOf(data.agent);
            const state = stage && stage.dataset.state === 'warn' ? 'warn' : 'done';
            setStage(data.agent, state, data.duration_ms);
            log(AGENT_NAME[data.agent], `${data.message || 'finished.'} (${formatDuration(data.duration_ms)})`);
        },

        planner_preview(data) {
            const questions = data.sub_questions || [];
            $('stat-subq').textContent = String(questions.length);
            const list = el('ol', 'plan');
            questions.forEach(q => {
                const item = el('li');
                item.appendChild(el('span', 'plan-q', q.question || String(q)));
                if (q.research_goal) item.appendChild(el('span', 'plan-goal', q.research_goal));
                list.appendChild(item);
            });
            setStageBody('planner',
                data.strategy ? el('p', 'detail-lead', data.strategy) : null,
                (data.domains || []).length ? tagList(data.domains) : null,
                list);
        },

        researcher_task_complete(data) {
            const lane = watch.lanes.get(data.task_id);
            if (!lane) return;
            lane.querySelector('.lane-count').textContent = plural(data.source_count, 'source');
            if (!data.source_count) lane.dataset.state = 'empty';
        },

        researcher_preview(data) {
            $('stat-sources').textContent = String(data.source_count);
            const list = el('ul', 'sources-mini');
            (data.sources || []).forEach(source => {
                const item = el('li');
                item.appendChild(externalLink(source.url, source.title || source.url));
                item.appendChild(el('span', 'cred', percent(source.credibility)));
                list.appendChild(item);
            });
            const remaining = data.source_count - (data.sources || []).length;
            setStageBody('collector',
                el('p', 'detail-lead',
                    `${plural(data.source_count, 'unique source')} from ${plural(data.total_searches, 'search')}.`),
                list,
                remaining > 0 ? el('p', 'detail-note', `and ${remaining} more`) : null);
        },

        analyst_preview(data) {
            const strengths = Object.entries(data.evidence_strength || {}).map(([level, count]) => `${count} ${level}`);
            const facts = [plural(data.insight_count, 'insight')];
            if (data.contradictions) facts.push(plural(data.contradictions, 'contradiction'));
            if (data.knowledge_gaps) facts.push(plural(data.knowledge_gaps, 'knowledge gap'));
            setStageBody('analyst',
                meter(data.confidence, 'Confidence'),
                el('p', 'detail-lead', `${facts.join(', ')}.`),
                strengths.length ? tagList(strengths.map(s => `${s} evidence`)) : null);
        },

        critic_preview(data) {
            const verdicts = Object.entries(data.verdicts || {})
                .map(([verdict, count]) => `${count} ${verdict.replace(/_/g, ' ')}`);
            const decision = el('p', 'decision', data.should_replan
                ? 'Not good enough. Sending the run back to the Planner.'
                : 'Quality is sufficient. Proceeding to the Writer.');
            decision.dataset.tone = data.should_replan ? 'warn' : 'good';
            let gaps = null;
            if ((data.gaps || []).length) {
                gaps = el('div', 'detail-block');
                gaps.appendChild(el('h4', null, 'Gaps found'));
                gaps.appendChild(tagList(data.gaps, 'bullets'));
            }
            setStageBody('critic',
                meter(data.quality_score, 'Quality'),
                meter(data.source_adequacy, 'Source adequacy'),
                verdicts.length ? tagList(verdicts) : null,
                gaps, decision);
            if (data.should_replan) {
                setStage('critic', 'warn');
                watch.pendingGaps = data.gaps || [];
            }
        },

        replan(data) {
            watch.round = data.replan_count + 1;
            $('stat-round').textContent = String(watch.round);
            $('stat-sources').textContent = '-';
            const gaps = watch.pendingGaps || [];
            addLoop(`Replan ${data.replan_count}`, gaps.length
                ? `Critic found ${plural(gaps.length, 'gap')}; planning round ${watch.round}.`
                : `Planning round ${watch.round}.`);
            log('Critic', `ordered a replan. Round ${watch.round} begins.`, 'warn');
            // The Critic keeps its verdict on screen until it runs again.
            resetStages(AGENTS.map(agent => agent.id).filter(id => id !== 'critic'));
        },

        writer_preview(data) {
            setStageBody('writer',
                el('p', 'detail-title', data.title),
                el('p', 'detail-note',
                    `${plural(data.findings, 'key finding')}, ${plural(data.sources, 'cited source')}.`));
        },

        editor_preview(data) {
            const verdict = el('p', 'decision', data.passed ? 'Passed the QA gate.' : 'Failed the QA gate.');
            verdict.dataset.tone = data.passed ? 'good' : 'warn';
            const sections = Object.entries(data.section_completeness || {});
            let checklist = null;
            if (sections.length) {
                checklist = el('ul', 'checklist');
                sections.forEach(([name, ok]) => {
                    const item = el('li', null, name.replace(/_/g, ' '));
                    item.dataset.ok = ok ? 'true' : 'false';
                    checklist.appendChild(item);
                });
            }
            setStageBody('editor',
                meter(data.citation_coverage, 'Citation coverage'),
                el('p', 'detail-note', `${data.word_count} words. Structure ${data.schema_valid ? 'valid' : 'invalid'}.`),
                checklist,
                (data.warnings || []).length ? tagList(data.warnings, 'bullets') : null,
                verdict);
            if (!data.passed) setStage('editor', 'warn');
        },

        writer_retry(data) {
            addLoop('Writer retry', 'Editor rejected the draft; one rewrite allowed.');
            log('Editor', data.reason || 'sent the draft back to the Writer.', 'warn');
            resetStages(['writer']);
        },

        human_approval_required(data) {
            $('run-state').textContent = 'Waiting for approval';
            const list = $('approval-plan');
            clear(list);
            ((data.plan || {}).sub_questions || []).forEach(q => {
                list.appendChild(el('li', null, q.question || String(q)));
            });
            $('approve-btn').disabled = $('reject-btn').disabled = false;
            $('approval').hidden = false;
            if (watch.live) $('approve-btn').focus();
            log('System', 'Paused for your approval.', 'warn');
        },

        approved() {
            $('approval').hidden = true;
            $('run-state').textContent = 'Researching';
            log('System', 'Approved. Continuing.');
        },

        complete(data) {
            watch.finished = true;
            watch.endedAt = Date.now();
            $('run-state').textContent = 'Complete';
            log('System', 'Report ready.');
            const current = watch;
            // Let the last stage settle visibly before switching views.
            setTimeout(() => {
                if (watch !== current) return;
                const run = { ...current.run, status: 'completed', report: data.report };
                stopWatching();
                showReport(run);
                refreshRuns();
            }, current.live ? 650 : 0);
        },

        cancelled(data) {
            $('run-state').textContent = 'Cancelled';
            log('System', data.message || 'Cancelled.', 'warn');
            showStopped('This run was cancelled', data.message || 'The run was stopped before it finished.', true);
        },

        error(data) {
            $('run-state').textContent = 'Failed';
            log('System', data.message || 'The run failed.', 'warn');
            showStopped('The run did not finish', data.message || 'The research run failed.', true);
        },
    };

    function startFanOut() {
        watch.lanes.clear();
        watch.lanesDone = 0;
        setStageBody('researcher', el('ul', 'lanes'));
        log('Researchers', 'fan-out started.');
    }

    function addLane(taskId, question) {
        const lanes = stageOf('researcher').querySelector('.lanes');
        const lane = el('li', 'lane');
        lane.dataset.state = 'active';
        lane.appendChild(el('span', 'lane-dot'));
        lane.appendChild(el('span', 'lane-q', question || 'Sub-question'));
        lane.appendChild(el('span', 'lane-count'));
        lanes.appendChild(lane);
        watch.lanes.set(taskId, lane);
    }

    function finishLane(taskId, durationMs) {
        const lane = watch.lanes.get(taskId);
        if (!lane || lane.dataset.finished) return;
        if (lane.dataset.state !== 'empty') lane.dataset.state = 'done';
        lane.dataset.finished = 'true';
        watch.lanesDone += 1;
        log('Researcher', `finished "${lane.querySelector('.lane-q').textContent}" in ${formatDuration(durationMs)}.`);
        if (watch.lanesDone === watch.lanes.size) setStage('researcher', 'done', durationMs);
    }

    async function decide(action) {
        if (!watch) return;
        $('approve-btn').disabled = $('reject-btn').disabled = true;
        try {
            await api(`/runs/${watch.run.id}/decision`, { method: 'POST', body: { action } });
        } catch (error) {
            showToast(error.message);
            $('approve-btn').disabled = $('reject-btn').disabled = false;
        }
    }

    // ----------------------------------------------------------------------
    // Report
    // ----------------------------------------------------------------------
    function section(title, id) {
        const node = el('section', 'report-section');
        if (id) node.id = id;
        node.appendChild(el('h2', null, title));
        return node;
    }

    function listOf(tag, className, items) {
        const list = el(tag, className);
        items.forEach(item => list.appendChild(renderInline(String(item), el('li'))));
        return list;
    }

    function showReport(run) {
        const report = run.report;
        currentReport = { run };
        const article = $('report');
        clear(article);
        const meta = report.metadata || {};

        const header = el('header', 'report-head');
        header.appendChild(el('p', 'eyebrow', 'Research report'));
        const title = el('h1', null, report.title || 'Research report');
        title.id = 'report-title';
        header.appendChild(title);
        header.appendChild(el('p', 'report-question', `Research question: ${run.query}`));

        const stats = el('dl', 'report-stats');
        const stat = (label, value) => {
            if (!value) return;
            const cell = el('div');
            cell.appendChild(el('dt', null, label));
            cell.appendChild(el('dd', null, value));
            stats.appendChild(cell);
        };
        stat('Date', formatDate(run.created_at));
        stat('Confidence', report.confidence_level ? capitalize(report.confidence_level) : '');
        stat('Duration', meta.total_duration_seconds ? formatDuration(meta.total_duration_seconds * 1000) : '');
        stat('Sources cited', String((report.sources || []).length));
        stat('Searches', String(meta.total_searches ?? '-'));
        stat('Replans', String(meta.replan_count ?? 0));
        header.appendChild(stats);
        article.appendChild(header);

        if (report.executive_summary) {
            const summary = el('section', 'report-section executive');
            summary.appendChild(el('h2', null, 'Executive summary'));
            summary.appendChild(renderInline(report.executive_summary, el('p')));
            article.appendChild(summary);
        }
        if ((report.quality_warnings || []).length) {
            const warnings = section('Editor warnings');
            warnings.classList.add('warnings');
            warnings.appendChild(el('p', 'detail-note',
                'The report did not fully pass the QA gate. Read it with these caveats in mind.'));
            warnings.appendChild(listOf('ul', 'bullets', report.quality_warnings));
            article.appendChild(warnings);
        }
        if (report.summary) {
            const analysis = section('Analysis');
            analysis.appendChild(renderMarkdown(report.summary));
            article.appendChild(analysis);
        }
        [['key_findings', 'Key findings', 'ol', 'findings'],
            ['recommendations', 'Recommendations', 'ol', 'findings'],
            ['risks', 'Risks and concerns', 'ul', 'bullets'],
            ['contradictions', 'Contradictions', 'ul', 'bullets'],
            ['knowledge_gaps', 'Areas for further research', 'ul', 'bullets'],
        ].forEach(([key, heading, tag, className]) => {
            if ((report[key] || []).length) {
                const block = section(heading);
                block.appendChild(listOf(tag, className, report[key]));
                article.appendChild(block);
            }
        });
        if (report.confidence_rationale) {
            const confidence = section('Confidence assessment');
            const text = el('p', 'confidence');
            if (report.confidence_level) {
                text.appendChild(el('strong', null, `${capitalize(report.confidence_level)} confidence. `));
            }
            renderInline(report.confidence_rationale, text);
            confidence.appendChild(text);
            article.appendChild(confidence);
        }
        if (report.methodology) {
            const method = section('Methodology');
            method.appendChild(renderMarkdown(report.methodology));
            article.appendChild(method);
        }
        if ((report.sources || []).length) {
            const sources = section('Sources', 'sources');
            const list = el('ol', 'sources');
            report.sources.forEach((source, position) => {
                const item = el('li');
                if (source && typeof source === 'object') {
                    const index = source.index || position + 1;
                    item.id = `source-${index}`;
                    item.appendChild(el('span', 'source-index', String(index)));
                    const info = el('div', 'source-info');
                    info.appendChild(el('span', 'source-title', source.title || 'Untitled source'));
                    info.appendChild(externalLink(source.url, source.url, 'source-url'));
                    item.appendChild(info);
                    item.appendChild(el('span', 'cred', percent(source.credibility)));
                } else {
                    item.appendChild(el('span', 'source-index', String(position + 1)));
                    item.appendChild(externalLink(source, String(source), 'source-url'));
                }
                list.appendChild(item);
            });
            sources.appendChild(list);
            article.appendChild(sources);
        }

        Array.from(article.children).forEach((child, index) => {
            child.style.setProperty('--i', String(Math.min(index, 8)));
        });
        $('dl-md').href = `/runs/${encodeURIComponent(run.id)}/export/markdown`;
        $('dl-html').href = `/runs/${encodeURIComponent(run.id)}/export/html`;
        document.title = `${report.title || 'Research report'} - DeepAgent Lab`;
        showView('report', report.title || 'Research report');
    }

    function reportToText(report) {
        const lines = [`# ${report.title || 'Research report'}`, ''];
        if (report.executive_summary) lines.push(`> ${report.executive_summary}`, '');
        if (report.summary) lines.push('## Analysis', report.summary, '');
        [['key_findings', 'Key findings'], ['recommendations', 'Recommendations'],
            ['risks', 'Risks and concerns'], ['contradictions', 'Contradictions'],
            ['knowledge_gaps', 'Areas for further research']].forEach(([key, heading]) => {
            if ((report[key] || []).length) {
                lines.push(`## ${heading}`);
                report[key].forEach(item => lines.push(`- ${item}`));
                lines.push('');
            }
        });
        if (report.methodology) lines.push('## Methodology', report.methodology, '');
        if ((report.sources || []).length) {
            lines.push('## Sources');
            report.sources.forEach((s, i) => {
                lines.push(typeof s === 'object' ? `[${s.index || i + 1}] ${s.title} ${s.url}` : `- ${s}`);
            });
        }
        return lines.join('\n');
    }

    async function copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            const area = el('textarea', 'sr-only');
            area.value = text;
            area.setAttribute('readonly', '');
            document.body.appendChild(area);
            area.select();
            let copied = false;
            try { copied = document.execCommand('copy'); } catch (err) { copied = false; }
            area.remove();
            return copied;
        }
    }

    function setDownloadMenu(open) {
        $('download-list').hidden = !open;
        $('download-btn').setAttribute('aria-expanded', String(open));
    }

    function jumpToSource(hash) {
        const target = document.getElementById(hash.slice(1));
        if (!target) return;
        target.scrollIntoView({ block: 'center', behavior: 'smooth' });
        target.classList.remove('is-flash');
        void target.offsetWidth;
        target.classList.add('is-flash');
    }

    // ----------------------------------------------------------------------
    // Users (admin)
    // ----------------------------------------------------------------------
    async function loadUsers() {
        let body;
        try {
            body = await api('/admin/users');
        } catch (error) {
            if (error.status !== 401) showError(error.message);
            return;
        }
        const tbody = $('users-body');
        clear(tbody);
        $('users-empty').hidden = body.users.length > 0;
        body.users.forEach(account => tbody.appendChild(buildUserRow(account)));
    }

    async function patchUser(account, values, successMessage) {
        try {
            await api(`/admin/users/${account.id}`, { method: 'PATCH', body: values });
            showToast(successMessage);
        } catch (error) {
            showToast(error.message);
        }
        loadUsers();
    }

    function buildUserRow(account) {
        const isSelf = account.id === user.id;
        const row = el('tr');

        const who = el('td');
        who.appendChild(el('span', 'user-name', account.full_name || account.email));
        if (account.full_name) who.appendChild(el('span', 'user-email', account.email));
        if (isSelf) who.appendChild(el('span', 'role-tag', 'You'));
        row.appendChild(who);

        const roleCell = el('td');
        const select = el('select', 'select');
        select.setAttribute('aria-label', `Role for ${account.email}`);
        ['user', 'admin'].forEach(role => {
            const option = el('option', null, capitalize(role));
            option.value = role;
            option.selected = account.role === role;
            select.appendChild(option);
        });
        select.disabled = isSelf;
        select.addEventListener('change', () => patchUser(account, { role: select.value },
            `${account.email} is now ${select.value === 'admin' ? 'an admin' : 'a regular user'}`));
        roleCell.appendChild(select);
        row.appendChild(roleCell);

        const statusCell = el('td');
        const chip = el('span', 'status-chip', account.is_active ? 'Active' : 'Deactivated');
        chip.dataset.tone = account.is_active ? 'good' : 'bad';
        statusCell.appendChild(chip);
        row.appendChild(statusCell);

        row.appendChild(el('td', 'num', String(account.run_count)));
        row.appendChild(el('td', null, formatDate(account.created_at)));

        const actions = el('td', 'row-actions');
        if (!isSelf) {
            actions.appendChild(button(account.is_active ? 'Deactivate' : 'Reactivate', 'btn btn-ghost btn-sm',
                () => patchUser(account, { is_active: !account.is_active },
                    `${account.email} ${account.is_active ? 'deactivated' : 'reactivated'}`)));
            const remove = button('', 'icon-btn', () => confirmDanger(
                'Delete this account?',
                `${account.email} and all of their research (${plural(account.run_count, 'run')}) are permanently deleted.`,
                'Delete account', async () => {
                    try {
                        await api(`/admin/users/${account.id}`, { method: 'DELETE' });
                        showToast('Account deleted');
                    } catch (error) {
                        showToast(error.message);
                    }
                    loadUsers();
                }));
            remove.setAttribute('aria-label', `Delete ${account.email}`);
            remove.appendChild(icon('trash'));
            actions.appendChild(remove);
        }
        row.appendChild(actions);
        return row;
    }

    // ----------------------------------------------------------------------
    // Shell: sidebar drawer, theme, server status
    // ----------------------------------------------------------------------
    function openSidebar() {
        sidebar.classList.add('is-open');
        $('scrim').hidden = false;
        $('menu-btn').setAttribute('aria-expanded', 'true');
        $('sidebar-close').focus();
    }

    function closeSidebar() {
        if (!sidebar.classList.contains('is-open')) return;
        sidebar.classList.remove('is-open');
        $('scrim').hidden = true;
        $('menu-btn').setAttribute('aria-expanded', 'false');
    }

    function toggleTheme() {
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('theme', next); } catch (e) { /* storage blocked */ }
        syncThemeButton();
    }

    function syncThemeButton() {
        const dark = document.documentElement.dataset.theme === 'dark';
        $('theme-btn').setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    }

    async function checkHealth() {
        const status = $('system-status');
        const text = $('system-status-text');
        const note = $('setup-note');
        try {
            const response = await fetch('/health');
            if (!response.ok) throw new Error('unhealthy');
            const body = await response.json();
            const config = body.config || {};
            $('mode-badge').hidden = !config.mock_mode;
            if (config.max_query_length) {
                maxQueryLength = config.max_query_length;
                input.maxLength = maxQueryLength;
                updateCounter();
            }
            const problems = [];
            if (!config.database_ready) {
                problems.push(config.database_configured
                    ? 'The database is configured but not answering. Check that supabase/schema.sql has been run.'
                    : 'The database is not configured (SUPABASE_URL and keys are missing from .env).');
            }
            if (!config.llm_ready) {
                problems.push('No LLM API key is configured, so research runs cannot start. Add GROQ_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY to .env and restart the server.');
            }
            (config.problems || []).forEach(problem => problems.push(problem));
            status.dataset.state = problems.length ? 'setup' : 'online';
            text.textContent = problems.length ? 'Setup needed'
                : `Online, ${config.mock_mode ? 'mock agents' : (config.llm_providers || []).join(' + ') || 'live'}`;
            note.hidden = problems.length === 0;
            note.textContent = problems.length ? `Setup needed. ${problems.join(' ')}` : '';
            return config;
        } catch (e) {
            status.dataset.state = 'offline';
            text.textContent = 'Server unreachable';
            return null;
        }
    }

    // ----------------------------------------------------------------------
    // Start-up
    // ----------------------------------------------------------------------
    function wireApp() {
        $('research-form').addEventListener('submit', event => { event.preventDefault(); submitQuery(); });
        input.addEventListener('input', () => { updateCounter(); if (!$('query-error').hidden) setFieldError(''); });
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                submitQuery();
            }
        });
        document.querySelectorAll('.chip[data-query]').forEach(chip => {
            chip.addEventListener('click', () => {
                input.value = chip.dataset.query;
                updateCounter();
                setFieldError('');
                input.focus();
            });
        });

        $('run-search').addEventListener('input', renderDashboard);
        document.querySelectorAll('.segmented button').forEach(option => {
            option.addEventListener('click', () => {
                runFilter = option.dataset.filter;
                document.querySelectorAll('.segmented button').forEach(other => {
                    other.setAttribute('aria-pressed', String(other === option));
                });
                renderDashboard();
            });
        });

        $('cancel-btn').addEventListener('click', async () => {
            if (!watch) return;
            $('cancel-btn').disabled = true;
            try {
                await api(`/runs/${watch.run.id}/cancel`, { method: 'POST' });
            } catch (error) {
                showToast(error.message);
                $('cancel-btn').disabled = false;
            }
        });
        $('approve-btn').addEventListener('click', () => decide('approve'));
        $('reject-btn').addEventListener('click', () => decide('reject'));
        $('retry-btn').addEventListener('click', () => { if (watch) retryRun(watch.run.id); });
        $('stopped-delete-btn').addEventListener('click', () => { if (watch) askDeleteRun(watch.run); });

        $('copy-btn').addEventListener('click', async () => {
            if (!currentReport) return;
            const copied = await copyText(reportToText(currentReport.run.report));
            showToast(copied ? 'Report copied to clipboard' : 'Copy failed: clipboard access was denied');
        });
        $('download-btn').addEventListener('click', event => {
            event.stopPropagation();
            setDownloadMenu($('download-list').hidden);
        });
        $('dl-pdf').addEventListener('click', () => { setDownloadMenu(false); window.print(); });
        ['dl-html', 'dl-md'].forEach(id => $(id).addEventListener('click', () => {
            setDownloadMenu(false);
            showToast('Download started');
        }));
        $('delete-btn').addEventListener('click', () => { if (currentReport) askDeleteRun(currentReport.run); });
        $('report').addEventListener('click', event => {
            const cite = event.target.closest('a.cite');
            if (!cite) return;
            event.preventDefault();
            jumpToSource(cite.getAttribute('href'));
        });

        // Act on the form's submit event (synchronous) rather than the dialog's
        // close event, which browsers may defer until the next rendered frame.
        confirmDialog.querySelector('form').addEventListener('submit', event => {
            const action = confirmAction;
            confirmAction = null;
            if (event.submitter && event.submitter.value === 'confirm' && action) action();
        });
        confirmDialog.addEventListener('cancel', () => { confirmAction = null; });

        $('menu-btn').addEventListener('click', openSidebar);
        $('sidebar-close').addEventListener('click', closeSidebar);
        $('scrim').addEventListener('click', closeSidebar);
        $('theme-btn').addEventListener('click', toggleTheme);
        document.addEventListener('click', event => {
            if (!event.target.closest('#download-menu')) setDownloadMenu(false);
        });
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;
            closeSidebar();
            if (!$('download-list').hidden) { setDownloadMenu(false); $('download-btn').focus(); }
        });
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && user) refreshRuns();
        });
        window.addEventListener('hashchange', route);
    }

    async function init() {
        buildExplainer();
        syncThemeButton();
        updateCounter();
        wireAuth();
        wireApp();

        const link = await consumeEmailLink();
        if (link && link.user) { signedIn(link.user); return; }

        try {
            const body = await api('/auth/me');
            signedIn(body.user);
        } catch (error) {
            signedOut(link && link.notice);
            if (link && link.error) setAuthMessage('auth-error', link.error);
            else if (error.status === 503 || error.status === 0) setAuthMessage('auth-error', error.message);
        }
    }

    init();
})();
