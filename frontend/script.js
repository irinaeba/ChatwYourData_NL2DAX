// ============================================================
// Data Analytics Chat Assistant - Frontend Logic
// ============================================================

const API_BASE_URL = '';
let conversationHistory = [];

// ============================================================
// Initialization
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {
    console.log('🚀 Initializing Data Analytics Assistant...');
    
    await checkServiceHealth();
    setupEventListeners();
    
    const input = document.getElementById('question-input');
    if (input) input.focus();
});

// ============================================================
// Event Listeners
// ============================================================

function setupEventListeners() {
    const submitBtn = document.getElementById('submit-btn');
    const questionInput = document.getElementById('question-input');
    
    console.log('Setting up event listeners...');
    console.log('Submit button found:', !!submitBtn);
    console.log('Question input found:', !!questionInput);
    
    if (submitBtn) {
        submitBtn.addEventListener('click', function(e) {
            console.log('Send button clicked!');
            e.preventDefault();
            handleSubmitQuery();
        });
        console.log('Click listener added to submit button');
    }
    
    if (questionInput) {
        questionInput.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') {
                handleSubmitQuery();
            }
        });
        
        // Auto-resize textarea
        questionInput.addEventListener('input', () => {
            questionInput.style.height = 'auto';
            questionInput.style.height = Math.min(questionInput.scrollHeight, 120) + 'px';
        });
    }
}

// ============================================================
// Service Health Check
// ============================================================

async function checkServiceHealth() {
    try {
        const response = await fetch(`${API_BASE_URL}/health`);
        const data = await response.json();
        
        const statusDot = document.getElementById('status-dot');
        const statusLabel = document.getElementById('status-label');
        
        if (data.status === 'ready') {
            if (statusDot) statusDot.className = 'dot ready';
            if (statusLabel) statusLabel.textContent = 'Ready';
            console.log('✓ Service is ready');
        } else {
            if (statusDot) statusDot.className = 'dot loading';
            if (statusLabel) statusLabel.textContent = 'Loading...';
            console.log('⏳ Service is initializing...');
            setTimeout(checkServiceHealth, 2000);
        }
    } catch (error) {
        console.error('❌ Health check failed:', error);
        
        const statusDot = document.getElementById('status-dot');
        const statusLabel = document.getElementById('status-label');
        
        if (statusDot) statusDot.className = 'dot error';
        if (statusLabel) statusLabel.textContent = 'Offline';
        
        setTimeout(checkServiceHealth, 5000);
    }
}

// ============================================================
// Query Submission
// ============================================================

function handleSubmitQuery() {
    console.log('handleSubmitQuery called');
    const questionInput = document.getElementById('question-input');
    const question = questionInput.value.trim();
    
    console.log('Question value:', question);
    
    if (!question) {
        alert('Please enter a question');
        return;
    }
    
    console.log('Calling submitQuery...');
    submitQuery(question);
}

async function submitQuery(question) {
    const messagesContainer = document.getElementById('messages-container');
    const welcomePanel = document.getElementById('welcome-panel');
    const submitBtn = document.getElementById('submit-btn');
    const questionInput = document.getElementById('question-input');
    
    // Hide welcome panel and show messages
    if (welcomePanel) welcomePanel.style.display = 'none';
    if (messagesContainer) messagesContainer.style.display = 'flex';
    
    // Add user message
    addMessageToUI('user', question);
    
    // Clear and disable input
    questionInput.value = '';
    questionInput.style.height = 'auto';
    submitBtn.disabled = true;
    questionInput.disabled = true;
    
    // Add loading indicator
    addLoadingMessage();
    
    try {
        const showDAX = document.getElementById('show-dax-checkbox').checked;
        const includeExplanation = document.getElementById('include-explanation-checkbox').checked;
        
        const response = await fetch(`${API_BASE_URL}/query`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                question: question,
                include_raw_dax: showDAX,
                include_explanation: includeExplanation
            })
        });
        
        const data = await response.json();
        
        removeLoadingMessage();
        
        // Check if clarification is needed
        if (data.clarification_needed && data.clarification_suggestions) {
            const message = data.clarification_message || 'Could you clarify your question?';
            addClarificationMessage(message, data.clarification_suggestions);
        } else if (data.success) {
            let responseText = data.formatted_answer || 'No answer available';
            
            // Add metadata
            let metadata = '';
            if (data.rows_returned !== null && data.rows_returned !== undefined) {
                metadata += `📊 Rows returned: ${data.rows_returned}`;
            }
            if (data.execution_time_ms) {
                if (metadata) metadata += ' | ';
                metadata += `⏱️ Execution time: ${data.execution_time_ms.toFixed(0)}ms`;
            }
            
            if (showDAX && data.raw_dax) {
                responseText += `\n\n**Query:**\n\`\`\`\n${data.raw_dax}\n\`\`\``;
            }
            
            if (metadata) {
                responseText += `\n\n_${metadata}_`;
            }
            
            addMessageToUI('bot', responseText);
        } else {
            // Check if re-authentication is required
            if (data.requires_reauth) {
                addAuthErrorMessage(data.error || 'Your session has expired. Please refresh to re-authenticate.');
            } else {
                addMessageToUI('bot', `❌ Error: ${data.error || 'Unknown error occurred'}`);
            }
        }
    } catch (error) {
        console.error('❌ Query failed:', error);
        removeLoadingMessage();
        addMessageToUI('bot', '❌ Failed to process query. Please try again.');
    } finally {
        submitBtn.disabled = false;
        questionInput.disabled = false;
        questionInput.focus();
    }
}

// ============================================================
// Message Display
// ============================================================

function addMessageToUI(role, content) {
    const messagesContainer = document.getElementById('messages-container');
    if (!messagesContainer) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = markdownToHtml(content);
    
    const meta = document.createElement('div');
    meta.className = 'message-meta';
    meta.textContent = new Date().toLocaleTimeString([], { 
        hour: '2-digit', 
        minute: '2-digit' 
    });
    
    contentDiv.appendChild(bubble);
    contentDiv.appendChild(meta);
    
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);
    
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function addLoadingMessage() {
    const messagesContainer = document.getElementById('messages-container');
    if (!messagesContainer) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message bot';
    messageDiv.id = 'loading-message';
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '🤖';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    
    const typingIndicator = document.createElement('div');
    typingIndicator.className = 'typing-indicator';
    typingIndicator.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
    
    bubble.appendChild(typingIndicator);
    contentDiv.appendChild(bubble);
    
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);
    
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function removeLoadingMessage() {
    const loadingMessage = document.getElementById('loading-message');
    if (loadingMessage) {
        loadingMessage.remove();
    }
}

function addAuthErrorMessage(errorMessage) {
    const messagesContainer = document.getElementById('messages-container');
    if (!messagesContainer) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message bot';
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '🔐';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble auth-error';
    
    // Create error content with refresh button
    const errorContent = document.createElement('div');
    errorContent.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
            <span style="font-size: 24px;">⚠️</span>
            <strong>Authentication Required</strong>
        </div>
        <p style="margin: 10px 0;">${errorMessage}</p>
        <p style="margin: 10px 0; font-size: 14px; color: #666;">
            Your Power BI session has expired. Click the button below to refresh and re-authenticate.
        </p>
    `;
    
    const refreshButton = document.createElement('button');
    refreshButton.className = 'refresh-auth-btn';
    refreshButton.innerHTML = '🔄 Refresh to Re-authenticate';
    refreshButton.style.cssText = `
        margin-top: 10px;
        padding: 10px 20px;
        background-color: #0078d4;
        color: white;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 8px;
    `;
    refreshButton.addEventListener('click', () => {
        window.location.reload();
    });
    refreshButton.addEventListener('mouseenter', () => {
        refreshButton.style.backgroundColor = '#005a9e';
    });
    refreshButton.addEventListener('mouseleave', () => {
        refreshButton.style.backgroundColor = '#0078d4';
    });
    
    errorContent.appendChild(refreshButton);
    bubble.appendChild(errorContent);
    
    const meta = document.createElement('div');
    meta.className = 'message-meta';
    meta.textContent = new Date().toLocaleTimeString([], { 
        hour: '2-digit', 
        minute: '2-digit' 
    });
    
    contentDiv.appendChild(bubble);
    contentDiv.appendChild(meta);
    
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);
    
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// ============================================================
// Sample Questions
// ============================================================

function askSampleQuestion(question) {
    const input = document.getElementById('question-input');
    if (input) {
        input.value = question;
        input.focus();
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        
        // Trigger submit
        setTimeout(handleSubmitQuery, 100);
    }
}

// ============================================================
// Clarification Chips
// ============================================================

function addClarificationMessage(message, suggestions) {
    const messagesContainer = document.getElementById('messages-container');
    if (!messagesContainer) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message bot';
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '🤖';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    
    // Clarification text
    const textP = document.createElement('p');
    textP.textContent = message;
    textP.style.marginBottom = '12px';
    bubble.appendChild(textP);
    
    // Suggestion chips container
    const chipsContainer = document.createElement('div');
    chipsContainer.className = 'clarification-chips';
    
    suggestions.forEach(suggestion => {
        const chip = document.createElement('button');
        chip.className = 'clarification-chip';
        chip.textContent = suggestion;
        chip.addEventListener('click', () => {
            // Disable all chips after one is clicked
            const allChips = chipsContainer.querySelectorAll('.clarification-chip');
            allChips.forEach(c => {
                c.disabled = true;
                c.classList.add('chip-disabled');
            });
            chip.classList.add('chip-selected');
            chip.classList.remove('chip-disabled');
            
            // Submit the selected suggestion as a new question
            const input = document.getElementById('question-input');
            if (input) {
                input.value = suggestion;
                setTimeout(handleSubmitQuery, 100);
            }
        });
        chipsContainer.appendChild(chip);
    });
    
    bubble.appendChild(chipsContainer);
    
    const meta = document.createElement('div');
    meta.className = 'message-meta';
    meta.textContent = new Date().toLocaleTimeString([], { 
        hour: '2-digit', 
        minute: '2-digit' 
    });
    
    contentDiv.appendChild(bubble);
    contentDiv.appendChild(meta);
    
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);
    
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// ============================================================
// Number Formatting for Table Cells
// ============================================================

function formatCellValue(value, header) {
    if (!value) return value;

    // Already has % sign (e.g. "39.9%") → round to integer
    if (/^-?\d+(\.\d+)?%$/.test(value)) {
        const num = parseFloat(value);
        return Math.round(num) + '%';
    }

    // Header hints at percentage (contains "%", "rate", "percent")
    const headerLower = (header || '').trim().toLowerCase();
    const isPercentageCol = headerLower.includes('%') || headerLower.includes('rate') || headerLower.includes('percent');

    // Pure number (possibly with existing commas stripped)
    const cleaned = value.replace(/,/g, '');
    if (/^-?\d+(\.\d+)?$/.test(cleaned)) {
        const num = parseFloat(cleaned);

        if (isPercentageCol) {
            // Values like 0.40 (ratio) → display as 40%
            if (Math.abs(num) <= 1.0001) {
                return Math.round(num * 100) + '%';
            }
            return Math.round(num) + '%';
        }

        // Integer values → commas only, no decimals
        if (Number.isInteger(num)) {
            return num.toLocaleString('en-US');
        }

        // Decimal values → 1 decimal place with comma separators
        return num.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    }

    return value;
}

// ============================================================
// Markdown to HTML
// ============================================================

function markdownToHtml(markdown) {
    if (!markdown) return '';
    
    let html = escapeHtml(markdown);
    
    // Code blocks first (preserve content)
    const codeBlocks = [];
    html = html.replace(/```[\s\S]*?```/g, (match) => {
        const code = match.replace(/```/g, '').trim();
        const index = codeBlocks.length;
        codeBlocks.push(`<pre><code>${code}</code></pre>`);
        return `__CODE_BLOCK_${index}__`;
    });
    
    // Headers
    html = html.replace(/^### (.*?)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.*?)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.*?)$/gm, '<h1>$1</h1>');
    
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    
    // Bold
    html = html.replace(/\*\*([^\*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    
    // Italic
    html = html.replace(/\*([^\*]+)\*/g, '<em>$1</em>');
    html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
    
    // Tables
    html = html.replace(/\|(.+)\n\|[\s\-:|]+\n((?:\|.+\n?)*)/g, (match) => {
        const lines = match.trim().split('\n');
        let table = '<table>';
        
        const headerCells = lines[0].split('|').filter(cell => cell.trim());
        table += '<thead><tr>';
        headerCells.forEach(cell => {
            table += `<th>${cell.trim()}</th>`;
        });
        table += '</tr></thead>';
        
        table += '<tbody>';
        for (let i = 2; i < lines.length; i++) {
            const cells = lines[i].split('|').filter(cell => cell.trim());
            if (cells.length > 0) {
                table += '<tr>';
                cells.forEach((cell, colIdx) => {
                    table += `<td>${formatCellValue(cell.trim(), headerCells[colIdx])}</td>`;
                });
                table += '</tr>';
            }
        }
        table += '</tbody></table>';
        
        return table;
    });
    
    // Lists
    html = html.replace(/^- (.*?)$/gm, '<li>$1</li>');
    html = html.replace(/^• (.*?)$/gm, '<li>$1</li>');
    
    // Paragraphs
    html = html.replace(/\n\n+/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    if (!html.startsWith('<')) {
        html = `<p>${html}</p>`;
    }
    
    // Clean up
    html = html.replace(/<p><\/p>/g, '');
    html = html.replace(/<p>(<h[1-6])/g, '$1');
    html = html.replace(/(<\/h[1-6]>)<\/p>/g, '$1');
    html = html.replace(/<p>(<pre>)/g, '$1');
    html = html.replace(/(<\/pre>)<\/p>/g, '$1');
    
    // Restore code blocks
    codeBlocks.forEach((code, index) => {
        html = html.replace(`__CODE_BLOCK_${index}__`, code);
    });
    
    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

console.log('✓ Data Analytics Assistant loaded');
