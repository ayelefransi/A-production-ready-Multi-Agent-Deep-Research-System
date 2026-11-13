/* ==========================================================================
   DeepAgent Lab - frontend
   No third-party scripts. Every piece of server or model text is inserted
   with textContent / DOM nodes, never innerHTML.
   ========================================================================== */

(function () {
    'use strict';

    // ----------------------------------------------------------------------
    // Pipeline definition (single source for the home explainer and the rail)
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

    const $ = id => document.getElementById(id);
    const SVG_NS = 'http://www.w3.org/2000/svg';

    // ----------------------------------------------------------------------
    // Small DOM helpers
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

    function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    const capitalize = text => String(text).charAt(0).toUpperCase() + String(text).slice(1);
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    const percent = v => `${Math.round((Number(v) || 0) * 100)}%`;

    function formatDuration(ms) {
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

    /** A 0..1 meter. Tone is derived from the value unless given. */
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
        // Set after insertion so the bar animates from zero.
        requestAnimationFrame(() => fill.style.setProperty('--v', String(v)));
        return wrap;
    }

    function tagList(items, className) {
        const list = el('ul', className || 'tags');
        items.forEach(item => list.appendChild(el('li', null, item)));
        return list;
    }

    // ----------------------------------------------------------------------
    // Safe, minimal Markdown -> DOM (headings, lists, bold/italic/code, links,
    // and [n] citations that jump to the matching source).
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
    // Elements
    // ----------------------------------------------------------------------
    const views = {
        home: $('view-home'), run: $('view-run'), report: $('view-report'), error: $('view-error'),
    };
    const form = $('research-form');
    const input = $('query-input');
    const submitBtn = $('submit-btn');
    const counter = $('query-counter');
    const queryError = $('query-error');
    const rail = $('rail');
    const logList = $('log');
    const loops = $('loops');
    const approval = $('approval');
    const sidebar = $('sidebar');
    const scrim = $('scrim');
    const menuBtn = $('menu-btn');
    const historyList = $('history-list');
    const toast = $('toast');
    const deleteDialog = $('delete-dialog');

    // ----------------------------------------------------------------------
    // State
    // ----------------------------------------------------------------------
    let maxQueryLength = 500;
    let run = null;            // the in-flight run, or null
    let currentReport = null;  // { threadId, report } shown in the report view
    let lastQuery = '';
    let clockTimer = null;
    let toastTimer = null;

    // ----------------------------------------------------------------------
    // Views
    // ----------------------------------------------------------------------
    function showView(name, title) {
        Object.entries(views).forEach(([key, node]) => { node.hidden = key !== name; });
        const view = views[name];
        view.classList.remove('view-enter');
        void view.offsetWidth; // restart the entrance animation
        view.classList.add('view-enter');
        $('topbar-title').textContent = title;
        window.scrollTo(0, 0);
    }

    function goHome(keepQuery) {
        document.title = 'DeepAgent Lab - Multi-Agent Research';
        showView('home', 'New research');
        if (!keepQuery) { input.value = ''; updateCounter(); }
        setFieldError('');
        markActiveHistory(null);
        input.focus();
    }

    function showToast(message) {
        toast.textContent = message;
        toast.classList.add('is-visible');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2600);
    }

    // ----------------------------------------------------------------------
    // Home: explainer + form
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
        counter.textContent = `${length} / ${maxQueryLength}`;
        counter.classList.toggle('is-near', length > maxQueryLength * 0.9);
        // Auto-grow with the text; an empty field falls back to its CSS height.
        input.style.height = '';
        if (length && input.scrollHeight > input.clientHeight) {
            input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
        }
    }

    function setFieldError(message) {
        queryError.textContent = message;
        queryError.hidden = !message;
        input.setAttribute('aria-invalid', message ? 'true' : 'false');
    }

    function validateQuery(raw) {
        const query = raw.replace(/\s+/g, ' ').trim();
        if (query.length < 3) return { error: 'Enter a question of at least 3 characters.' };
        if (query.length > maxQueryLength) return { error: `Keep the question under ${maxQueryLength} characters.` };
        return { query };
    }

    function submitQuery() {
        if (run) return;
        const result = validateQuery(input.value);
        if (result.error) {
            setFieldError(result.error);
            input.focus();
            return;
        }
        setFieldError('');
        startResearch(result.query);
    }

    // ----------------------------------------------------------------------
    // Run: pipeline rail
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
    const STATUS_TEXT = { waiting: 'Waiting', active: 'Working', done: 'Done', warn: 'Sent back' };

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
            stage.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
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
        const elapsed = run ? (performance.now() - run.startedAt) / 1000 : 0;
        item.appendChild(el('time', 'log-time', `${elapsed.toFixed(1)}s`));
        const text = el('span', 'log-text');
        text.appendChild(el('strong', null, source));
        text.appendChild(document.createTextNode(` ${message}`));
        item.appendChild(text);
        logList.appendChild(item);
        logList.scrollTop = logList.scrollHeight;
    }

    function addLoop(label, detail, tone) {
        const pill = el('div', 'loop');
        pill.dataset.tone = tone;
        pill.appendChild(icon('loop'));
        pill.appendChild(el('strong', null, label));
        if (detail) pill.appendChild(el('span', null, detail));
        loops.appendChild(pill);
    }

    function tickClock() {
        if (!run) return;
        const now = performance.now();
        $('run-clock').textContent = formatDuration(now - run.startedAt);
        rail.querySelectorAll('.stage[data-state="active"]').forEach(stage => {
            const startedAt = Number(stage.dataset.startedAt);
            if (startedAt) stage.querySelector('.stage-time').textContent = formatDuration(now - startedAt);
        });
    }

    // ----------------------------------------------------------------------
    // Run: lifecycle
    // ----------------------------------------------------------------------
    function startResearch(query) {
        lastQuery = query;
        run = {
            query,
            threadId: null,
            startedAt: performance.now(),
            controller: new AbortController(),
            round: 1,
            lanes: new Map(),   // researcher task_id -> lane element
            lanesDone: 0,
            finished: false,
        };

        buildRail();
        clear(logList);
        clear(loops);
        approval.hidden = true;
        $('run-query').textContent = query;
        $('run-state').textContent = 'Researching';
        $('run-clock').textContent = '0.0s';
        $('stat-round').textContent = '1';
        $('stat-subq').textContent = '-';
        $('stat-sources').textContent = '-';
        $('cancel-btn').hidden = false;
        submitBtn.disabled = true;
        markActiveHistory(null);
        showView('run', 'Research in progress');
        log('System', 'Run started.');

        clearInterval(clockTimer);
        clockTimer = setInterval(tickClock, 100);

        streamResearch(run).catch(error => {
            if (error.name === 'AbortError') return;
            failRun(error.message || 'The connection to the server was lost.');
        });
    }

    function endRun() {
        clearInterval(clockTimer);
        clockTimer = null;
        run = null;
        submitBtn.disabled = false;
    }

    function failRun(message) {
        endRun();
        $('error-message').textContent = message;
        showView('error', 'Run failed');
        $('retry-btn').focus();
        loadHistory();
    }

    function cancelResearch() {
        if (!run) return;
        run.controller.abort();
        endRun();
        goHome(true);
        input.value = lastQuery;
        updateCounter();
        showToast('Research cancelled');
        loadHistory();
    }

    async function streamResearch(activeRun) {
        const response = await fetch('/research/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: activeRun.query }),
            signal: activeRun.controller.signal,
        });

        if (!response.ok) throw new Error(await errorMessage(response));

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
            const frames = buffer.split('\n\n');
            buffer = frames.pop();
            frames.forEach(frame => dispatchFrame(frame, activeRun));
        }
        if (buffer.trim()) dispatchFrame(buffer, activeRun);

        if (run === activeRun && !activeRun.finished) {
            failRun('The server closed the connection before the report was ready.');
        }
    }

    async function errorMessage(response) {
        let detail = '';
        try {
            const body = await response.json();
            if (typeof body.detail === 'string') detail = body.detail;
            else if (Array.isArray(body.detail) && body.detail[0]) detail = String(body.detail[0].msg || '');
        } catch (e) { /* not JSON */ }
        if (response.status === 429) return detail || 'Too many requests. Wait a moment and try again.';
        return detail || `The server returned an error (HTTP ${response.status}).`;
    }

    function dispatchFrame(frame, activeRun) {
        if (run !== activeRun) return; // cancelled or superseded
        let name = 'message';
        const dataLines = [];
        frame.split('\n').forEach(line => {
            if (line.startsWith('event:')) name = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
        });
        if (!dataLines.length) return;
        let data;
        try { data = JSON.parse(dataLines.join('\n')); } catch (e) { return; }
        const handler = handlers[name];
        if (handler) handler(data);
    }

    // ----------------------------------------------------------------------
    // Run: event handlers (names match utils/sse.py)
    // ----------------------------------------------------------------------
    const handlers = {
        research_start(data) {
            run.threadId = data.thread_id;
            if (data.mock_mode) log('System', 'Mock mode: agents return canned output, no API calls.', 'warn');
        },

        agent_start(data) {
            if (data.agent === 'researcher') {
                if (run.lanes.size === run.lanesDone) startFanOut();
                addLane(data.task_id, data.sub_question);
                if (stageOf('researcher').dataset.state !== 'active') setStage('researcher', 'active');
                return;
            }
            setStage(data.agent, 'active');
            log(AGENT_NAME[data.agent], 'started.');
        },

        agent_complete(data) {
            if (data.agent === 'researcher') {
                finishLane(data.task_id, data.duration_ms);
                return;
            }
            // A critic that orders a replan, or an editor that fails QA, is
            // marked "sent back" by its preview event; keep that state.
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
            const lane = run.lanes.get(data.task_id);
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
            const strengths = Object.entries(data.evidence_strength || {})
                .map(([level, count]) => `${count} ${level}`);
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
            const decision = el('p', 'decision',
                data.should_replan
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
                gaps,
                decision);
            if (data.should_replan) {
                setStage('critic', 'warn');
                run.pendingGaps = data.gaps || [];
            }
        },

        replan(data) {
            run.round = data.replan_count + 1;
            $('stat-round').textContent = String(run.round);
            $('stat-sources').textContent = '-';
            const gaps = run.pendingGaps || [];
            addLoop(`Replan ${data.replan_count}`,
                gaps.length ? `Critic found ${plural(gaps.length, 'gap')}; planning round ${run.round}.`
                    : `Planning round ${run.round}.`, 'warn');
            log('Critic', `ordered a replan. Round ${run.round} begins.`, 'warn');
            // The new round reruns everything. The Critic keeps its verdict on
            // screen until it runs again, so the reason for the loop stays visible.
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
                el('p', 'detail-note',
                    `${data.word_count} words. Structure ${data.schema_valid ? 'valid' : 'invalid'}.`),
                checklist,
                (data.warnings || []).length ? tagList(data.warnings, 'bullets') : null,
                verdict);
            if (!data.passed) setStage('editor', 'warn');
        },

        writer_retry(data) {
            addLoop('Writer retry', 'Editor rejected the draft; one rewrite allowed.', 'warn');
            log('Editor', data.reason || 'sent the draft back to the Writer.', 'warn');
            // The Editor keeps its failed verdict visible until it runs again.
            resetStages(['writer']);
        },

        human_approval_required(data) {
            run.finished = true; // the stream ends here by design
            clearInterval(clockTimer);
            $('run-state').textContent = 'Waiting for approval';
            $('cancel-btn').hidden = true;
            const list = $('approval-plan');
            clear(list);
            ((data.plan || {}).sub_questions || []).forEach(q => {
                list.appendChild(el('li', null, q.question || String(q)));
            });
            approval.hidden = false;
            approval.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            $('approve-btn').focus();
            log('System', 'Paused for human approval.', 'warn');
            loadHistory();
        },

        complete(data) {
            run.finished = true;
            $('run-state').textContent = 'Complete';
            log('System', 'Report ready.');
            const activeRun = run;
            // Let the last stage settle visibly before switching views.
            setTimeout(() => {
                if (run !== activeRun) return;
                const query = activeRun.query;
                endRun();
                showReport(data.thread_id, data.report, { query });
                loadHistory();
            }, 650);
        },

        error(data) {
            run.finished = true;
            failRun(data.message || 'The research run failed.');
        },
    };

    function startFanOut() {
        run.lanes.clear();
        run.lanesDone = 0;
        setStageBody('researcher', el('ul', 'lanes'));
        log('Researchers', 'fan-out started.');
    }

    function addLane(taskId, question) {
        const lanes = stageOf('researcher').querySelector('.lanes');
        const lane = el('li', 'lane');
        lane.dataset.state = 'active';
        lane.dataset.startedAt = String(performance.now());
        lane.appendChild(el('span', 'lane-dot'));
        lane.appendChild(el('span', 'lane-q', question || 'Sub-question'));
        lane.appendChild(el('span', 'lane-count'));
        lanes.appendChild(lane);
        run.lanes.set(taskId, lane);
    }

    function finishLane(taskId, durationMs) {
        const lane = run.lanes.get(taskId);
        if (!lane || lane.dataset.state === 'done') return;
        if (lane.dataset.state !== 'empty') lane.dataset.state = 'done';
        lane.dataset.finished = 'true';
        run.lanesDone += 1;
        log('Researcher', `finished "${lane.querySelector('.lane-q').textContent}" in ${formatDuration(durationMs)}.`);
        if (run.lanesDone === run.lanes.size) {
            const startedAt = Number(stageOf('researcher').dataset.startedAt);
            setStage('researcher', 'done', startedAt ? performance.now() - startedAt : durationMs);
        }
    }

    // ----------------------------------------------------------------------
    // Human approval
    // ----------------------------------------------------------------------
    async function resolveApproval(action) {
        if (!run || !run.threadId) return;
        const activeRun = run;
        const approveBtn = $('approve-btn');
        const rejectBtn = $('reject-btn');
        approveBtn.disabled = rejectBtn.disabled = true;
        if (action === 'approve') {
            approveBtn.textContent = 'Continuing';
            $('run-state').textContent = 'Analysing and writing';
            ['collector', 'analyst', 'critic', 'writer', 'editor'].forEach(a => setStage(a, 'active'));
            log('System', 'Approved. Finishing the run.');
        }
        try {
            const response = await fetch('/research/resume', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ thread_id: activeRun.threadId, action }),
            });
            if (!response.ok) throw new Error(await errorMessage(response));
            const body = await response.json();
            if (run !== activeRun) return;
            if (body.status === 'completed' && body.report) {
                const query = activeRun.query;
                endRun();
                showReport(body.thread_id, body.report, { query });
            } else if (body.status === 'rejected') {
                endRun();
                goHome(true);
                showToast('Run rejected and stopped');
            } else {
                throw new Error(body.message || 'The run is still waiting for approval.');
            }
            loadHistory();
        } catch (error) {
            if (run === activeRun) failRun(error.message);
        } finally {
            approveBtn.disabled = rejectBtn.disabled = false;
            approveBtn.textContent = 'Approve and continue';
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

    function markdownList(items, className) {
        const list = el('ul', className);
        items.forEach(item => list.appendChild(renderInline(String(item), el('li'))));
        return list;
    }

    function showReport(threadId, report, info) {
        const context = info || {};
        currentReport = { threadId, report };
        const article = $('report');
        clear(article);
        const meta = report.metadata || {};

        const header = el('header', 'report-head');
        header.appendChild(el('p', 'eyebrow', 'Research report'));
        const title = el('h1', null, report.title || 'Research report');
        title.id = 'report-title';
        header.appendChild(title);
        if (context.query) header.appendChild(el('p', 'report-question', `Research question: ${context.query}`));

        const stats = el('dl', 'report-stats');
        const stat = (label, value) => {
            const cell = el('div');
            cell.appendChild(el('dt', null, label));
            cell.appendChild(el('dd', null, value));
            stats.appendChild(cell);
        };
        const created = context.createdAt ? new Date(context.createdAt) : new Date();
        if (!Number.isNaN(created.getTime())) {
            stat('Date', created.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }));
        }
        if (report.confidence_level) stat('Confidence', capitalize(report.confidence_level));
        if (meta.total_duration_seconds) stat('Duration', formatDuration(meta.total_duration_seconds * 1000));
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
            warnings.appendChild(markdownList(report.quality_warnings, 'bullets'));
            article.appendChild(warnings);
        }

        if (report.summary) {
            const summary = section('Analysis');
            summary.appendChild(renderMarkdown(report.summary));
            article.appendChild(summary);
        }

        if ((report.key_findings || []).length) {
            const findings = section('Key findings');
            const list = el('ol', 'findings');
            report.key_findings.forEach(item => list.appendChild(renderInline(String(item), el('li'))));
            findings.appendChild(list);
            article.appendChild(findings);
        }

        if ((report.recommendations || []).length) {
            const recommendations = section('Recommendations');
            const list = el('ol', 'findings');
            report.recommendations.forEach(item => list.appendChild(renderInline(String(item), el('li'))));
            recommendations.appendChild(list);
            article.appendChild(recommendations);
        }

        [['risks', 'Risks and concerns'], ['contradictions', 'Contradictions'],
            ['knowledge_gaps', 'Areas for further research']].forEach(([key, heading]) => {
            if ((report[key] || []).length) {
                const block = section(heading);
                block.appendChild(markdownList(report[key], 'bullets'));
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

        // Stagger the entrance of the sections.
        Array.from(article.children).forEach((child, index) => {
            child.style.setProperty('--i', String(Math.min(index, 8)));
        });

        const exportBase = `/research/${encodeURIComponent(threadId)}/export`;
        $('dl-md').href = `${exportBase}/markdown`;
        $('dl-html').href = `${exportBase}/html`;
        document.title = `${report.title || 'Research report'} - DeepAgent Lab`;
        showView('report', report.title || 'Research report');
        markActiveHistory(threadId);
    }

    function reportToText(report) {
        const lines = [`# ${report.title || 'Research report'}`, ''];
        if (report.executive_summary) lines.push(`> ${report.executive_summary}`, '');
        if (report.summary) lines.push('## Analysis', report.summary, '');
        [['key_findings', 'Key findings'], ['recommendations', 'Recommendations'],
            ['risks', 'Risks and concerns'],
            ['contradictions', 'Contradictions'], ['knowledge_gaps', 'Areas for further research']]
            .forEach(([key, heading]) => {
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

    /** Clipboard API first; fall back to a selection copy where it is blocked. */
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

    async function copyReport() {
        if (!currentReport) return;
        const copied = await copyText(reportToText(currentReport.report));
        showToast(copied ? 'Report copied to clipboard' : 'Copy failed: clipboard access was denied');
    }

    async function deleteReport() {
        if (!currentReport) return;
        const { threadId } = currentReport;
        try {
            const response = await fetch(`/research/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
            if (!response.ok && response.status !== 404) throw new Error(await errorMessage(response));
            currentReport = null;
            await loadHistory();
            goHome(false);
            showToast('Report deleted');
        } catch (error) {
            showToast(`Delete failed: ${error.message}`);
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
    // History
    // ----------------------------------------------------------------------
    const STATUS_LABEL = {
        completed: 'Completed', running: 'Running', failed: 'Failed', cancelled: 'Cancelled',
        rejected: 'Rejected', paused_for_approval: 'Awaiting approval',
    };

    async function loadHistory() {
        let data;
        try {
            const response = await fetch('/research/history');
            if (!response.ok) return;
            data = await response.json();
        } catch (e) {
            return; // the status pill already reports an offline server
        }
        const sessions = data.sessions || [];
        clear(historyList);
        $('history-empty').hidden = sessions.length > 0;
        $('history-count').textContent = sessions.length ? String(data.total) : '';

        sessions.forEach(session => {
            const item = el('button', 'history-item');
            item.type = 'button';
            item.dataset.threadId = session.thread_id;
            item.dataset.status = session.status;
            item.appendChild(el('span', 'history-query', session.query));
            const meta = el('span', 'history-meta');
            meta.appendChild(el('span', 'history-status', STATUS_LABEL[session.status] || session.status));
            meta.appendChild(el('span', null, formatTimeAgo(session.created_at)));
            item.appendChild(meta);
            item.addEventListener('click', () => openSession(session));
            historyList.appendChild(item);
        });
        markActiveHistory(currentReport && !views.report.hidden ? currentReport.threadId : null);
    }

    function markActiveHistory(threadId) {
        historyList.querySelectorAll('.history-item').forEach(item => {
            const active = item.dataset.threadId === threadId;
            item.classList.toggle('is-active', active);
            if (active) item.setAttribute('aria-current', 'true');
            else item.removeAttribute('aria-current');
        });
    }

    async function openSession(session) {
        closeSidebar();
        if (run) {
            showToast('Cancel the current run before opening another report');
            return;
        }
        if (!session.has_report) {
            goHome(true);
            input.value = session.query;
            updateCounter();
            showToast('That run has no report. The question is ready to run again.');
            return;
        }
        try {
            const response = await fetch(`/research/${encodeURIComponent(session.thread_id)}`);
            if (!response.ok) throw new Error(await errorMessage(response));
            const body = await response.json();
            showReport(session.thread_id, body.report, { query: body.query, createdAt: body.created_at });
        } catch (error) {
            showToast(`Could not open the report: ${error.message}`);
            loadHistory();
        }
    }

    // ----------------------------------------------------------------------
    // Sidebar drawer (mobile), theme, server status
    // ----------------------------------------------------------------------
    function openSidebar() {
        sidebar.classList.add('is-open');
        scrim.hidden = false;
        menuBtn.setAttribute('aria-expanded', 'true');
        $('sidebar-close').focus();
    }

    function closeSidebar() {
        if (!sidebar.classList.contains('is-open')) return;
        sidebar.classList.remove('is-open');
        scrim.hidden = true;
        menuBtn.setAttribute('aria-expanded', 'false');
        menuBtn.focus();
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
        try {
            const response = await fetch('/health');
            if (!response.ok) throw new Error('unhealthy');
            const body = await response.json();
            const config = body.config || {};
            status.dataset.state = 'online';
            text.textContent = `Online, v${body.version}`;
            $('mode-badge').hidden = !config.mock_mode;
            if (config.max_query_length) {
                maxQueryLength = config.max_query_length;
                input.maxLength = maxQueryLength;
                updateCounter();
            }
        } catch (e) {
            status.dataset.state = 'offline';
            text.textContent = 'Server unreachable';
        }
    }

    // ----------------------------------------------------------------------
    // Wiring
    // ----------------------------------------------------------------------
    function init() {
        buildExplainer();
        syncThemeButton();
        updateCounter();

        form.addEventListener('submit', event => { event.preventDefault(); submitQuery(); });
        input.addEventListener('input', () => { updateCounter(); if (!queryError.hidden) setFieldError(''); });
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

        $('cancel-btn').addEventListener('click', cancelResearch);
        $('approve-btn').addEventListener('click', () => resolveApproval('approve'));
        $('reject-btn').addEventListener('click', () => resolveApproval('reject'));
        $('retry-btn').addEventListener('click', () => {
            if (lastQuery) startResearch(lastQuery); else goHome(false);
        });
        $('error-back-btn').addEventListener('click', () => {
            goHome(true);
            input.value = lastQuery;
            updateCounter();
        });

        $('back-btn').addEventListener('click', () => goHome(false));
        $('copy-btn').addEventListener('click', copyReport);
        $('download-btn').addEventListener('click', event => {
            event.stopPropagation();
            setDownloadMenu($('download-list').hidden);
        });
        $('dl-pdf').addEventListener('click', () => { setDownloadMenu(false); window.print(); });
        ['dl-html', 'dl-md'].forEach(id => $(id).addEventListener('click', () => {
            setDownloadMenu(false);
            showToast('Download started');
        }));
        document.addEventListener('click', event => {
            if (!event.target.closest('#download-menu')) setDownloadMenu(false);
        });
        $('delete-btn').addEventListener('click', () => deleteDialog.showModal());
        deleteDialog.addEventListener('close', () => {
            if (deleteDialog.returnValue === 'confirm') deleteReport();
            deleteDialog.returnValue = '';
        });
        $('report').addEventListener('click', event => {
            const cite = event.target.closest('a.cite');
            if (!cite) return;
            event.preventDefault();
            jumpToSource(cite.getAttribute('href'));
        });

        $('new-research-btn').addEventListener('click', () => {
            closeSidebar();
            if (run) { showToast('A run is in progress. Cancel it to start another.'); return; }
            goHome(false);
        });
        menuBtn.addEventListener('click', openSidebar);
        $('sidebar-close').addEventListener('click', closeSidebar);
        scrim.addEventListener('click', closeSidebar);
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;
            closeSidebar();
            if (!$('download-list').hidden) { setDownloadMenu(false); $('download-btn').focus(); }
        });
        $('theme-btn').addEventListener('click', toggleTheme);

        checkHealth();
        loadHistory();
    }

    init();
})();
