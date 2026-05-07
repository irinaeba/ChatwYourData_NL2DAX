// frontend/auth.js
// MSAL.js Authentication Module for Production
// Handles user authentication via Entra ID (Azure AD)
// Uses BFF + OBO pattern for secure Power BI access

// Global MSAL instance
let msalInstance = null;
let authConfig = null;

/**
 * Initialize MSAL with configuration from backend
 */
async function initializeAuth() {
    try {
        // Fetch auth config from backend
        const response = await fetch('/auth/config');
        if (!response.ok) {
            throw new Error('Failed to fetch auth configuration');
        }
        
        authConfig = await response.json();
        console.log('Auth config loaded:', authConfig.clientId);
        
        // Initialize MSAL
        const msalConfig = {
            auth: {
                clientId: authConfig.clientId,
                authority: authConfig.authority,
                redirectUri: window.location.origin,
                postLogoutRedirectUri: window.location.origin,
            },
            cache: {
                // Use sessionStorage - cleared when browser closes (more secure)
                cacheLocation: "sessionStorage",
                storeAuthStateInCookie: false,
            },
            system: {
                loggerOptions: {
                    loggerCallback: (level, message, containsPii) => {
                        if (containsPii) return;
                        switch (level) {
                            case msal.LogLevel.Error:
                                console.error('[MSAL]', message);
                                break;
                            case msal.LogLevel.Warning:
                                console.warn('[MSAL]', message);
                                break;
                            case msal.LogLevel.Info:
                                console.info('[MSAL]', message);
                                break;
                            case msal.LogLevel.Verbose:
                                console.debug('[MSAL]', message);
                                break;
                        }
                    },
                    logLevel: msal.LogLevel.Warning,
                },
            },
        };
        
        // Create MSAL instance
        msalInstance = new msal.PublicClientApplication(msalConfig);
        
        // Handle redirect callback (for redirect flow)
        await msalInstance.initialize();
        const response2 = await msalInstance.handleRedirectPromise();
        if (response2) {
            console.log('Redirect login successful');
            onLoginSuccess(response2);
        }
        
        // Check if user is already logged in
        const accounts = msalInstance.getAllAccounts();
        if (accounts.length > 0) {
            console.log('Found existing account:', accounts[0].username);
            await tryAcquireTokenSilent();
        }
        
        return true;
        
    } catch (error) {
        console.error('Auth initialization failed:', error);
        return false;
    }
}

/**
 * Login with popup
 */
async function loginWithPopup() {
    if (!msalInstance) {
        console.error('MSAL not initialized');
        return null;
    }
    
    const loginRequest = {
        scopes: authConfig.scopes,
        prompt: "select_account",
    };
    
    try {
        showLoginProgress('Signing in...');
        const response = await msalInstance.loginPopup(loginRequest);
        onLoginSuccess(response);
        return response;
    } catch (error) {
        console.error('Login failed:', error);
        hideLoginProgress();
        
        if (error instanceof msal.BrowserAuthError) {
            if (error.errorCode === 'popup_window_error') {
                showLoginError('Popup blocked. Please allow popups and try again.');
            } else if (error.errorCode === 'user_cancelled') {
                showLoginError('Login cancelled.');
            } else {
                showLoginError('Login failed: ' + error.message);
            }
        } else {
            showLoginError('Login failed: ' + error.message);
        }
        return null;
    }
}

/**
 * Login with redirect (alternative to popup)
 */
async function loginWithRedirect() {
    if (!msalInstance) {
        console.error('MSAL not initialized');
        return;
    }
    
    const loginRequest = {
        scopes: authConfig.scopes,
    };
    
    try {
        await msalInstance.loginRedirect(loginRequest);
    } catch (error) {
        console.error('Redirect login failed:', error);
        showLoginError('Login failed: ' + error.message);
    }
}

/**
 * Try to acquire token silently (for returning users)
 */
async function tryAcquireTokenSilent() {
    if (!msalInstance) return null;
    
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) return null;
    
    const silentRequest = {
        scopes: authConfig.scopes,
        account: accounts[0],
    };
    
    try {
        const response = await msalInstance.acquireTokenSilent(silentRequest);
        onLoginSuccess(response);
        return response.accessToken;
    } catch (error) {
        console.log('Silent token acquisition failed, user needs to login');
        return null;
    }
}

/**
 * Get current access token (acquires silently if needed)
 */
async function getAccessToken() {
    if (!msalInstance) {
        throw new Error('Not authenticated');
    }
    
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) {
        throw new Error('No authenticated user');
    }
    
    const silentRequest = {
        scopes: authConfig.scopes,
        account: accounts[0],
    };
    
    try {
        // Try silent first
        const response = await msalInstance.acquireTokenSilent(silentRequest);
        return response.accessToken;
    } catch (error) {
        // If silent fails, try popup
        console.log('Silent token failed, trying popup');
        const response = await msalInstance.acquireTokenPopup(silentRequest);
        return response.accessToken;
    }
}

/**
 * Logout user
 */
async function logout() {
    if (!msalInstance) return;
    
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) return;
    
    try {
        await msalInstance.logoutPopup({
            account: accounts[0],
            postLogoutRedirectUri: window.location.origin,
        });
        onLogout();
    } catch (error) {
        console.error('Logout failed:', error);
        // Force local logout even if popup fails
        msalInstance.clearCache();
        onLogout();
    }
}

/**
 * Check if user is authenticated
 */
function isAuthenticated() {
    if (!msalInstance) return false;
    const accounts = msalInstance.getAllAccounts();
    return accounts.length > 0;
}

/**
 * Get current user info
 */
function getCurrentUser() {
    if (!msalInstance) return null;
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) return null;
    
    return {
        name: accounts[0].name,
        email: accounts[0].username,
        tenantId: accounts[0].tenantId,
    };
}

// ============================================================
// UI Callbacks - these update the UI based on auth state
// ============================================================

function onLoginSuccess(response) {
    console.log('Login successful:', response.account.username);
    hideLoginProgress();
    hideLoginScreen();
    showMainApp();
    updateUserInfo(response.account);
    
    // Initialize Power BI connection BEFORE enabling the query UI
    // This does OBO exchange + XMLA pre-connect so it's ready for the first query
    initializePowerBIConnection();
}

function onLogout() {
    hideMainApp();
    showLoginScreen();
    clearUserInfo();
}

/**
 * Initialize Power BI connection right after user authenticates.
 * Calls backend /auth/initialize which performs OBO exchange and
 * pre-connects to the Power BI XMLA endpoint.
 * 
 * BLOCKS the query UI until complete — shows a connecting status
 * and disables the input/button so the user can't query before
 * the XMLA connection is established.
 */
async function initializePowerBIConnection() {
    // Disable query UI while connecting
    const questionInput = document.getElementById('question-input');
    const submitBtn = document.getElementById('submit-btn');
    const statusDot = document.getElementById('status-dot');
    const statusLabel = document.getElementById('status-label');
    
    if (questionInput) {
        questionInput.disabled = true;
        questionInput.placeholder = 'Connecting to Power BI...';
    }
    if (submitBtn) submitBtn.disabled = true;
    if (statusDot) statusDot.className = 'dot loading';
    if (statusLabel) statusLabel.textContent = 'Connecting...';
    
    try {
        const token = await getAccessToken();
        const response = await fetch('/auth/initialize', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
            },
        });
        
        if (response.ok) {
            const result = await response.json();
            console.log('[OK] Power BI connection initialized:', result.message);
            if (result.warning) {
                console.warn('[WARN] Power BI init warning:', result.warning);
            }
            // Connection ready — enable query UI
            if (statusDot) statusDot.className = 'dot ready';
            if (statusLabel) statusLabel.textContent = 'Ready';
        } else {
            console.warn('[WARN] Power BI init failed:', response.status, await response.text());
            // Still enable UI — will connect on first query (slower)
            if (statusDot) statusDot.className = 'dot ready';
            if (statusLabel) statusLabel.textContent = 'Ready';
        }
    } catch (error) {
        console.warn('[WARN] Power BI init error (will retry on first query):', error.message);
        if (statusDot) statusDot.className = 'dot ready';
        if (statusLabel) statusLabel.textContent = 'Ready';
    } finally {
        // Always re-enable query UI
        if (questionInput) {
            questionInput.disabled = false;
            questionInput.placeholder = 'Ask a question about your data...';
            questionInput.focus();
        }
        if (submitBtn) submitBtn.disabled = false;
    }
}

function showLoginProgress(message) {
    const btn = document.getElementById('login-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> ' + message;
    }
}

function hideLoginProgress() {
    const btn = document.getElementById('login-btn');
    if (btn) {
        btn.disabled = false;
        btn.innerHTML = '🔐 Sign in with Microsoft';
    }
}

function showLoginError(message) {
    const errorEl = document.getElementById('login-error');
    if (errorEl) {
        errorEl.textContent = message;
        errorEl.style.display = 'block';
    }
}

function hideLoginError() {
    const errorEl = document.getElementById('login-error');
    if (errorEl) {
        errorEl.style.display = 'none';
    }
}

function showLoginScreen() {
    const loginScreen = document.getElementById('login-screen');
    if (loginScreen) loginScreen.style.display = 'flex';
}

function hideLoginScreen() {
    const loginScreen = document.getElementById('login-screen');
    if (loginScreen) loginScreen.style.display = 'none';
}

function showMainApp() {
    const mainApp = document.getElementById('main-app');
    if (mainApp) mainApp.style.display = 'flex';
}

function hideMainApp() {
    const mainApp = document.getElementById('main-app');
    if (mainApp) mainApp.style.display = 'none';
}

function updateUserInfo(account) {
    const userNameEl = document.getElementById('user-name');
    const userEmailEl = document.getElementById('user-email');
    const logoutBtn = document.getElementById('logout-btn');
    
    if (userNameEl) userNameEl.textContent = account.name || 'User';
    if (userEmailEl) userEmailEl.textContent = account.username || '';
    if (logoutBtn) logoutBtn.style.display = 'inline-block';
}

function clearUserInfo() {
    const userNameEl = document.getElementById('user-name');
    const userEmailEl = document.getElementById('user-email');
    const logoutBtn = document.getElementById('logout-btn');
    
    if (userNameEl) userNameEl.textContent = '';
    if (userEmailEl) userEmailEl.textContent = '';
    if (logoutBtn) logoutBtn.style.display = 'none';
}
