document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('research-form');
    const input = document.getElementById('query-input');
    const btn = document.getElementById('search-btn');
    const loadingContainer = document.getElementById('loading-container');
    const resultsContainer = document.getElementById('results-container');
    const reportContent = document.getElementById('report-content');
    const errorContainer = document.getElementById('error-container');
    const errorMessage = document.getElementById('error-message');
    
    // Formatting Helper using MarkedJS
    function renderReport(report) {
        let markdown = `# ${report.title}\n\n`;
        markdown += `> **Confidence Score:** ${Math.round((report.confidence_score || 0.9)*100)}%\n\n`;
        markdown += `## Summary\n${report.summary}\n\n`;
        
        markdown += `## Key Findings\n`;
        if (report.key_findings && report.key_findings.length > 0) {
            report.key_findings.forEach(finding => {
                markdown += `- ${finding}\n`;
            });
        }
        markdown += `\n`;

        if (report.risks && report.risks.length > 0) {
            markdown += `## Identified Risks\n`;
            report.risks.forEach(risk => {
                markdown += `- ${risk}\n`;
            });
            markdown += `\n`;
        }

        markdown += `## Sources\n`;
        if (report.sources && report.sources.length > 0) {
            report.sources.forEach((source, index) => {
                // Determine if source is http url
                if(source.startsWith('http')){
                    markdown += `${index + 1}. [${source}](${source})\n`;
                } else {
                    markdown += `${index + 1}. ${source}\n`;
                }
            });
        }

        // Parse and Purify
        const rawHtml = marked.parse(markdown);
        const cleanHtml = DOMPurify.sanitize(rawHtml);
        reportContent.innerHTML = cleanHtml;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const query = input.value.trim();
        if (!query) return;

        // Reset UI
        resultsContainer.classList.add('hidden');
        errorContainer.classList.add('hidden');
        loadingContainer.classList.remove('hidden');
        btn.disabled = true;
        
        // Simulating step animations (since SSE is not built, we estimate times)
        let step = 0;
        const steps = ['step-researcher', 'step-analyst', 'step-writer'];
        steps.forEach(s => document.getElementById(s).classList.remove('active'));
        document.getElementById(steps[0]).classList.add('active');

        const stepInterval = setInterval(() => {
            step++;
            if (step < steps.length) {
                steps.forEach(s => document.getElementById(s).classList.remove('active'));
                document.getElementById(steps[step]).classList.add('active');
            } else {
                clearInterval(stepInterval);
            }
        }, 12000); // Progress visually roughly every 12s

        try {
            const response = await fetch('/research', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ query })
            });

            clearInterval(stepInterval);

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.detail || `API Error: ${response.status}`);
            }

            const data = await response.json();
            
            if (data.status === 'paused_for_approval') {
                throw new Error("Human verification step triggered. This UI executes autonomous runs. Please disable REQUIRE_HUMAN_APPROVAL in .env.");
            }

            if (data.report) {
                renderReport(data.report);
                loadingContainer.classList.add('hidden');
                resultsContainer.classList.remove('hidden');
            } else {
                throw new Error("No report generated. Please check terminal logs.");
            }

        } catch (error) {
            clearInterval(stepInterval);
            loadingContainer.classList.add('hidden');
            errorContainer.classList.remove('hidden');
            errorMessage.textContent = error.message;
        } finally {
            btn.disabled = false;
        }
    });
});
