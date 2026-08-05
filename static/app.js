/* ==========================================================================
   DeepAgent Lab Frontend Application
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {

    // DOM Elements
    const form = document.getElementById('research-form');
    const input = document.getElementById('query-input');
    const searchBtn = document.getElementById('search-btn');

    const welcomeView = document.getElementById('welcome-view');
    const pipelineView = document.getElementById('pipeline-view');
    const reportView = document.getElementById('report-view');
    const errorView = document.getElementById('error-view');

    const pipelineQueryText = document.getElementById('pipeline-query-text');
    const cancelBtn = document.getElementById('cancel-btn');
    const iterationBadge = document.getElementById('iteration-badge');
    const iterationText = document.getElementById('iteration-text');
    const replanBadge = document.getElementById('replan-badge');
    const replanText = document.getElementById('replan-text');
    const parallelBadge = document.getElementById('parallel-badge');
    const parallelText = document.getElementById('parallel-text');

    const reportCards = document.getElementById('report-cards');
    const errorMessage = document.getElementById('error-message');
    const retryBtn = document.getElementById('retry-btn');

    const sidebar = document.getElementById('sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const newResearchBtn = document.getElementById('new-research-btn');
    const historyList = document.getElementById('history-list');
    const historyEmpty = document.getElementById('history-empty');

    const toast = document.getElementById('toast');
    const toastMessage = document.getElementById('toast-message');

    const btnBack = document.getElementById('btn-back');
    const btnCopy = document.getElementById('btn-copy');
    const btnExportMd = document.getElementById('btn-export-md');
    const btnDelete = document.getElementById('btn-delete');

    // Live Activity & Previews
    const activityEntries = document.getElementById('activity-entries');
    
    const previewPlanner = document.getElementById('preview-planner');
    const previewResearcher = document.getElementById('preview-researcher');
    const previewCritic = document.getElementById('preview-critic');
    const previewEditor = document.getElementById('preview-editor');
    
    const previewStrategy = document.getElementById('preview-strategy');
    const previewQuestions = document.getElementById('preview-questions');
    const previewSources = document.getElementById('preview-sources');
    const previewCriticContent = document.getElementById('preview-critic-content');
    const previewEditorContent = document.getElementById('preview-editor-content');

    // Meta elements
    const metaDuration = document.getElementById('meta-duration');
    const metaSearches = document.getElementById('meta-searches');
    const metaSources = document.getElementById('meta-sources');
    const metaIterations = document.getElementById('meta-iterations');
    const metaReplans = document.getElementById('meta-replans');

    // State
    let currentThreadId = null;
    let currentReport = null;
    let currentEventSource = null;
    let agentStartTimes = {};
    let researchStartTime = null;
    let lastQuery = '';
    let completedSubQuestions = 0;
    let totalSubQuestions = 0;

    // Agent Pipeline Nodes 
    const AGENTS = ['planner', 'researcher', 'collector', 'analyst', 'critic', 'writer', 'editor'];

    // Initialize
    loadHistory();

    // Event Listeners
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const query = input.value.trim();
        if (query) startResearch(query);
    });

    document.querySelectorAll('.hint-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            input.value = chip.dataset.query;
            input.focus();
        });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !reportView.classList.contains('hidden')) {
            showWelcome();
        }
    });

    cancelBtn.addEventListener('click', cancelResearch);
    retryBtn.addEventListener('click', () => {
        if (lastQuery) startResearch(lastQuery);
        else showWelcome();
    });

    btnBack.addEventListener('click', showWelcome);
    btnCopy.addEventListener('click', copyReport);
    btnExportMd.addEventListener('click', exportMarkdown);
    btnDelete.addEventListener('click', deleteSession);

    newResearchBtn.addEventListener('click', () => {
        showWelcome();
        input.focus();
        closeSidebarMobile();
    });

    sidebarToggle.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
    });

    mobileMenuBtn.addEventListener('click', () => {
        sidebar.classList.add('open');
        sidebarOverlay.classList.remove('hidden');
    });

    sidebarOverlay.addEventListener('click', closeSidebarMobile);

    // Core Functions

    function startResearch(query) {
        lastQuery = query;
        currentReport = null;
        currentThreadId = null;
        researchStartTime = Date.now();
        agentStartTimes = {};
        completedSubQuestions = 0;
        totalSubQuestions = 0;

        showView('pipeline');
        pipelineQueryText.textContent = query;
        resetPipeline();
        hideAllPreviews();
        activityEntries.innerHTML = '';
        logActivity('System', `Initiating research sequence for: "${query}"`);

        searchBtn.disabled = true;
        streamResearch(query);
    }

    function streamResearch(query) {
        if (currentEventSource) {
            currentEventSource.close();
            currentEventSource = null;
        }

        fetch('/research/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => {
                    throw new Error(err.detail || `HTTP ${response.status}`);
                });
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            function processStream() {
                reader.read().then(({ done, value }) => {
                    if (done) {
                        if (buffer.trim()) processSSEBuffer(buffer);
                        searchBtn.disabled = false;
                        return;
                    }

                    buffer += decoder.decode(value, { stream: true });
                    const parts = buffer.split('\n\n');
                    buffer = parts.pop();

                    for (const part of parts) {
                        if (part.trim()) processSSEEvent(part);
                    }

                    processStream();
                }).catch(err => {
                    showError(err.message);
                    searchBtn.disabled = false;
                });
            }

            processStream();
        })
        .catch(err => {
            showError(err.message);
            searchBtn.disabled = false;
        });
    }

    function processSSEBuffer(buffer) {
        const parts = buffer.split('\n\n');
        for (const part of parts) {
            if (part.trim()) processSSEEvent(part);
        }
    }

    function processSSEEvent(raw) {
        let eventType = 'message';
        let eventData = '';

        for (const line of raw.split('\n')) {
            if (line.startsWith('event: ')) {
                eventType = line.substring(7).trim();
            } else if (line.startsWith('data: ')) {
                eventData = line.substring(6);
            }
        }

        if (!eventData) return;

        let data;
        try {
            data = JSON.parse(eventData);
        } catch (e) {
            return;
        }

        handleSSEEvent(eventType, data);
    }

    function handleSSEEvent(event, data) {
        switch (event) {
            case 'research_start':
                currentThreadId = data.thread_id;
                logActivity('System', `Thread established: ${currentThreadId.substring(0,8)}`);
                break;

            case 'agent_start':
                setAgentState(data.agent, 'active');
                agentStartTimes[data.agent] = Date.now();
                logActivity(data.agent, `Started processing`);
                break;

            case 'agent_complete': {
                const agent = data.agent;
                if (agent !== 'researcher') {
                    setAgentState(agent, 'complete');
                }
                const duration = data.duration_ms;
                const timeEl = document.getElementById(`time-${agent}`);
                if (timeEl) timeEl.textContent = formatDuration(duration);
                logActivity(agent, `Completed in ${formatDuration(duration)}`);
                break;
            }

            case 'researcher_task_complete':
                completedSubQuestions++;
                updateParallelBadge();
                logActivity('researcher', `Finished sub-question: "${data.query}"`);
                if (completedSubQuestions === totalSubQuestions && totalSubQuestions > 0) {
                    setAgentState('researcher', 'complete');
                }
                break;

            case 'iteration':
                iterationBadge.classList.remove('hidden');
                iterationText.textContent = `Iteration ${data.iteration}`;
                replanBadge.classList.add('hidden');
                logActivity('System', `Starting iteration ${data.iteration}`);
                break;
                
            case 'replan':
                replanBadge.classList.remove('hidden');
                replanText.textContent = `Replan Loop (Critic Failed)`;
                ['planner', 'researcher', 'collector', 'analyst', 'critic', 'writer', 'editor'].forEach(a => setAgentState(a, 'waiting'));
                logActivity('critic', `Validation failed, triggering replan`);
                break;

            case 'editor_retry':
                logActivity('editor', `QA Gate failed: ${data.reason}. Retrying writer`);
                setAgentState('writer', 'waiting');
                setAgentState('editor', 'waiting');
                break;

            case 'planner_preview':
                showPlannerPreview(data);
                totalSubQuestions = (data.sub_questions || []).length;
                completedSubQuestions = 0;
                updateParallelBadge();
                break;

            case 'researcher_preview':
                showResearcherPreview(data);
                break;

            case 'critic_preview':
                showCriticPreview(data);
                break;
                
            case 'editor_preview':
                showEditorPreview(data);
                break;

            case 'complete':
                currentReport = data.report;
                currentThreadId = data.thread_id;
                searchBtn.disabled = false;
                showReport(data.report);
                loadHistory();
                logActivity('System', `Research complete`);
                break;

            case 'error':
                showError(data.message);
                searchBtn.disabled = false;
                break;
        }
    }

    // Pipeline State Management

    function resetPipeline() {
        AGENTS.forEach(agent => {
            setAgentState(agent, 'waiting');
            const timeEl = document.getElementById(`time-${agent}`);
            if (timeEl) timeEl.textContent = '';
        });
        iterationBadge.classList.add('hidden');
        replanBadge.classList.add('hidden');
        parallelBadge.classList.add('hidden');
    }

    function setAgentState(agentName, state) {
        const node = document.getElementById(`node-${agentName}`);
        if (!node) return;
        node.classList.remove('waiting', 'active', 'complete');
        node.classList.add(state);
    }
    
    function updateParallelBadge() {
        if (totalSubQuestions > 0) {
            parallelBadge.classList.remove('hidden');
            parallelText.textContent = `${completedSubQuestions} / ${totalSubQuestions} tasks`;
        }
    }

    function logActivity(agent, message) {
        const entry = document.createElement('div');
        entry.className = 'activity-entry';
        
        const time = new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        
        entry.innerHTML = `
            <span class="entry-time">[${time}]</span>
            <span class="entry-text"><strong>${capitalize(agent)}:</strong> ${message}</span>
        `;
        
        activityEntries.appendChild(entry);
        activityEntries.scrollTop = activityEntries.scrollHeight;
    }

    // Preview Panels

    function hideAllPreviews() {
        previewPlanner.classList.add('hidden');
        previewResearcher.classList.add('hidden');
        previewCritic.classList.add('hidden');
        previewEditor.classList.add('hidden');
    }

    function showPlannerPreview(data) {
        previewPlanner.classList.remove('hidden');
        previewStrategy.textContent = data.strategy || '';
        previewQuestions.innerHTML = '';
        (data.sub_questions || []).forEach(q => {
            const li = document.createElement('li');
            li.textContent = q;
            previewQuestions.appendChild(li);
        });
    }

    function showResearcherPreview(data) {
        previewResearcher.classList.remove('hidden');
        
        let count = parseInt((previewSources.dataset.count || 0)) + (data.source_count || 0);
        previewSources.dataset.count = count;
        
        let header = previewSources.querySelector('.preview-source-header');
        if (!header) {
            header = document.createElement('div');
            header.className = 'preview-source-header preview-source-item';
            previewSources.prepend(header);
        }
        header.innerHTML = `<strong>${count} total sources found</strong>`;

        (data.sources || []).forEach(s => {
            const item = document.createElement('div');
            item.className = 'preview-source-item';
            item.innerHTML = `
                <span></span>
                <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}</a>
            `;
            previewSources.appendChild(item);
        });
    }

    function showCriticPreview(data) {
        previewCritic.classList.remove('hidden');
        const score = Math.round((data.quality_score || 0) * 100);
        const barColor = score >= 70 ? 'var(--success)' : score >= 40 ? 'var(--warning)' : 'var(--error)';

        let gapsHtml = '';
        if (data.gaps && data.gaps.length > 0) {
            gapsHtml = `<div style="font-size:0.78rem;color:var(--text-secondary);margin-top:0.5rem;">
                <strong>Gaps:</strong> ${data.gaps.map(g => escapeHtml(g)).join(', ')}
            </div>`;
        }

        previewCriticContent.innerHTML = `
            <div class="quality-meter">
                <div class="quality-bar-bg">
                    <div class="quality-bar-fill" style="width:${score}%;background:${barColor};"></div>
                </div>
                <span class="quality-label" style="color:${barColor}">${score}%</span>
            </div>
            <div style="font-size:0.78rem;color:var(--text-secondary);">
                ${data.should_iterate ? 'Replan triggered due to low quality' : 'Quality sufficient - proceeding to write report.'}
            </div>
            ${gapsHtml}
        `;
    }
    
    function showEditorPreview(data) {
        previewEditor.classList.remove('hidden');
        
        const passedHtml = data.passed 
            ? `<span style="color:var(--success)">Passed QA Gate</span>` 
            : `<span style="color:var(--error)">Failed QA Gate</span>`;
            
        previewEditorContent.innerHTML = `
            <div style="font-weight:600;margin-bottom:0.5rem">${passedHtml}</div>
            <div style="font-size:0.8rem;color:var(--text-secondary)">
                <div>Citations: ${Math.round((data.citation_coverage || 0)*100)}%</div>
                <div>Word count: ${data.word_count || 0}</div>
                <div>Valid schema: ${data.schema_valid ? 'Yes' : 'No'}</div>
            </div>
        `;
    }

    // Report Rendering (Card-Based)

    function showReport(report) {
        showView('report');
        reportCards.innerHTML = '';

        const meta = report.metadata || {};
        const totalDuration = meta.total_duration_seconds
            ? formatDuration(meta.total_duration_seconds * 1000)
            : formatDuration(Date.now() - researchStartTime);
        metaDuration.textContent = totalDuration;
        metaSearches.textContent = `${meta.total_searches || '—'} searches`;
        metaSources.textContent = `${meta.total_sources_scanned || '—'} sources`;
        metaIterations.textContent = `${meta.iterations_taken || 1} iteration`;
        metaReplans.textContent = `${meta.replans_taken || 0} replans`;

        if (report.title) {
            addCard('card-title', '', `<h1>${escapeHtml(report.title)}</h1>`);
        }

        if (report.executive_summary) {
            addCard('card-executive', 'Executive Summary',
                `<p class="executive-text">${escapeHtml(report.executive_summary)}</p>`
            );
        }

        if (report.methodology) {
            addCard('', 'Methodology',
                `<div class="markdown-body">${renderMarkdown(report.methodology)}</div>`
            );
        }

        if (report.summary) {
            addCard('', 'Summary',
                `<div class="markdown-body">${renderMarkdown(report.summary)}</div>`
            );
        }

        if (report.key_findings && report.key_findings.length > 0) {
            const items = report.key_findings.map(f =>
                `<li>${renderMarkdown(f)}</li>`
            ).join('');
            addCard('', 'Key Findings', `<ul class="findings-list">${items}</ul>`);
        }
        
        if (report.quality_warnings && report.quality_warnings.length > 0) {
            const items = report.quality_warnings.map(w =>
                `<li>⚠️ ${escapeHtml(w)}</li>`
            ).join('');
            addCard('', 'Quality Warnings (Editor)', `<ul class="warnings-list">${items}</ul>`);
        }

        if (report.risks && report.risks.length > 0) {
            const items = report.risks.map(r =>
                `<li>${renderMarkdown(r)}</li>`
            ).join('');
            addCard('', 'Risks & Concerns', `<ul class="risks-list">${items}</ul>`);
        }

        if (report.contradictions && report.contradictions.length > 0) {
            const items = report.contradictions.map(c =>
                `<li>${escapeHtml(c)}</li>`
            ).join('');
            addCard('', 'Contradictions', `<ul class="contradictions-list">${items}</ul>`);
        }

        if (report.knowledge_gaps && report.knowledge_gaps.length > 0) {
            const items = report.knowledge_gaps.map(g =>
                `<li>${escapeHtml(g)}</li>`
            ).join('');
            addCard('', 'Areas for Further Research', `<ul class="gaps-list">${items}</ul>`);
        }

        if (report.sources && report.sources.length > 0) {
            let sourcesHtml = '<div class="sources-list">';
            for (const src of report.sources) {
                if (typeof src === 'object') {
                    const cred = src.credibility || 0;
                    const credClass = cred >= 0.7 ? 'credibility-high' : cred >= 0.4 ? 'credibility-medium' : 'credibility-low';
                    sourcesHtml += `
                        <div class="source-item">
                            <span class="source-index">[${src.index || ''}]</span>
                            <div class="source-info">
                                <div class="source-title">${escapeHtml(src.title || 'Unknown Source')}</div>
                                <a class="source-url" href="${escapeHtml(src.url || '#')}" target="_blank" rel="noopener">${escapeHtml(src.url || '')}</a>
                            </div>
                            <span class="source-credibility ${credClass}">${Math.round(cred * 100)}%</span>
                        </div>
                    `;
                } else {
                    sourcesHtml += `
                        <div class="source-item">
                            <span class="source-index">•</span>
                            <div class="source-info">
                                <a class="source-url" href="${escapeHtml(src)}" target="_blank" rel="noopener">${escapeHtml(src)}</a>
                            </div>
                        </div>
                    `;
                }
            }
            sourcesHtml += '</div>';
            addCard('', 'Sources', sourcesHtml);
        }
    }

    function addCard(extraClass, title, contentHtml) {
        const card = document.createElement('div');
        card.className = `report-card ${extraClass}`;

        let headerHtml = '';
        if (title) {
            headerHtml = `
                <div class="report-card-header">
                    <h2>${title}</h2>
                </div>
            `;
        }

        card.innerHTML = headerHtml + contentHtml;
        reportCards.appendChild(card);
    }

    // View Management

    function showView(view) {
        welcomeView.classList.add('hidden');
        pipelineView.classList.add('hidden');
        reportView.classList.add('hidden');
        errorView.classList.add('hidden');

        switch (view) {
            case 'welcome': welcomeView.classList.remove('hidden'); break;
            case 'pipeline': pipelineView.classList.remove('hidden'); break;
            case 'report': reportView.classList.remove('hidden'); break;
            case 'error': errorView.classList.remove('hidden'); break;
        }
    }

    function showWelcome() {
        showView('welcome');
        input.value = '';
        searchBtn.disabled = false;
    }

    function showError(message) {
        showView('error');
        errorMessage.textContent = message;
        searchBtn.disabled = false;
    }

    function cancelResearch() {
        if (currentEventSource) {
            currentEventSource.close();
            currentEventSource = null;
        }
        showWelcome();
        showToast('Research canceled');
    }

    // History

    async function loadHistory() {
        try {
            const res = await fetch('/research/history');
            if (!res.ok) return;
            const data = await res.json();

            if (!data.sessions || data.sessions.length === 0) {
                historyEmpty.classList.remove('hidden');
                historyList.querySelectorAll('.history-item').forEach(el => el.remove());
                return;
            }

            historyEmpty.classList.add('hidden');

            const existingItems = historyList.querySelectorAll('.history-item');
            existingItems.forEach(el => el.remove());

            for (const session of data.sessions) {
                const item = document.createElement('div');
                item.className = 'history-item';
                if (session.thread_id === currentThreadId) {
                    item.classList.add('active');
                }

                const statusClass = session.status || 'completed';
                const timeAgo = formatTimeAgo(session.created_at);

                item.innerHTML = `
                    <span class="history-query">${escapeHtml(session.query)}</span>
                    <div class="history-meta">
                        <span class="history-status ${statusClass}"></span>
                        <span>${timeAgo}</span>
                    </div>
                `;

                item.addEventListener('click', () => {
                    loadSession(session.thread_id);
                    closeSidebarMobile();
                });

                historyList.appendChild(item);
            }
        } catch (e) {
            console.warn('Failed to load history:', e);
        }
    }

    async function loadSession(threadId) {
        try {
            const res = await fetch(`/research/${threadId}`);
            if (!res.ok) throw new Error('Session not found');
            const session = await res.json();

            if (session.report) {
                currentReport = session.report;
                currentThreadId = threadId;
                showReport(session.report);
                historyList.querySelectorAll('.history-item').forEach(el => el.classList.remove('active'));
                
                const items = Array.from(historyList.querySelectorAll('.history-item'));
                const queryText = escapeHtml(session.query);
                const matchingItem = items.find(el => el.querySelector('.history-query').innerHTML === queryText);
                if (matchingItem) matchingItem.classList.add('active');
            }
        } catch (e) {
            showToast('Failed to load session');
        }
    }
    
    async function deleteSession() {
        if (!currentThreadId) return;
        if (!confirm('Are you sure you want to delete this research?')) return;
        
        try {
            showToast('Research deleted');
            currentThreadId = null;
            showWelcome();
            loadHistory();
        } catch (e) {
            showToast('Failed to delete');
        }
    }

    // Export & Copy

    async function copyReport() {
        if (!currentReport) return;
        try {
            const text = reportToPlaintext(currentReport);
            await navigator.clipboard.writeText(text);
            showToast('Report copied to clipboard');
        } catch (e) {
            showToast('Failed to copy');
        }
    }

    function exportMarkdown() {
        if (!currentThreadId) return;
        window.open(`/research/${currentThreadId}/export/markdown`, '_blank');
    }

    function reportToPlaintext(report) {
        let text = '';
        text += `# ${report.title || 'Research Report'}\n\n`;
        if (report.executive_summary) text += `> ${report.executive_summary}\n\n`;
        if (report.methodology) text += `## Methodology\n${report.methodology}\n\n`;
        if (report.summary) text += `## Summary\n${report.summary}\n\n`;
        if (report.key_findings) {
            text += `## Key Findings\n`;
            report.key_findings.forEach(f => text += `- ${f}\n`);
            text += '\n';
        }
        if (report.risks) {
            text += `## Risks\n`;
            report.risks.forEach(r => text += `- ${r}\n`);
            text += '\n';
        }
        if (report.sources) {
            text += `## Sources\n`;
            report.sources.forEach(s => {
                if (typeof s === 'object') {
                    text += `[${s.index}] ${s.title} ${s.url}\n`;
                } else {
                    text += `- ${s}\n`;
                }
            });
        }
        return text;
    }

    // --- Utilities ---

    function renderMarkdown(text) {
        if (!text) return '';
        const raw = marked.parse(text);
        return DOMPurify.sanitize(raw);
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function capitalize(str) {
        if (!str) return '';
        return str.charAt(0).toUpperCase() + str.slice(1);
    }

    function formatDuration(ms) {
        if (ms < 1000) return `${Math.round(ms)}ms`;
        const seconds = ms / 1000;
        if (seconds < 60) return `${seconds.toFixed(1)}s`;
        const mins = Math.floor(seconds / 60);
        const secs = Math.round(seconds % 60);
        return `${mins}m ${secs}s`;
    }

    function formatTimeAgo(isoString) {
        if (!isoString) return '';
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMin = Math.floor(diffMs / 60000);
        const diffHr = Math.floor(diffMin / 60);
        const diffDay = Math.floor(diffHr / 24);

        if (diffMin < 1) return 'just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        if (diffHr < 24) return `${diffHr}h ago`;
        if (diffDay < 7) return `${diffDay}d ago`;
        return date.toLocaleDateString();
    }

    function showToast(message) {
        toastMessage.textContent = message;
        toast.classList.remove('hidden');
        toast.classList.add('show');
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.classList.add('hidden'), 300);
        }, 2500);
    }

    function closeSidebarMobile() {
        sidebar.classList.remove('open');
        sidebarOverlay.classList.add('hidden');
    }
});
