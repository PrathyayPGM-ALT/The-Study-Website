const API = '/api';
let sb = null;

let currentUser = null;
let userProfile = null;

async function loadConfig() {
  const r = await fetch(API + '/config');
  const config = await r.json();
  sb = supabase.createClient(config.supabase_url, config.supabase_anon_key);
}

async function initAuth() {
  await loadConfig();
  const { data: { session } } = await sb.auth.getSession();
  if (session) {
    currentUser = session.user;
    await loadProfile(session.access_token);
    showApp();
  } else {
    showAuth();
  }

  sb.auth.onAuthStateChange(async (event, session) => {
    if (event === 'SIGNED_IN' && session) {
      currentUser = session.user;
      await loadProfile(session.access_token);
      showApp();
    } else if (event === 'SIGNED_OUT') {
      currentUser = null;
      userProfile = null;
      showAuth();
    }
  });
}

async function getToken() {
  const { data: { session } } = await sb.auth.getSession();
  return session?.access_token || '';
}

async function loadProfile(token) {
  try {
    const r = await fetch(API + '/me', { headers: { 'Authorization': 'Bearer ' + token } });
    if (r.ok) {
      userProfile = await r.json();
    }
  } catch {}
}

let authMode = 'login'; // 'login' | 'signup' | 'forgot'

function showAuth() {
  document.getElementById('auth-screen').classList.remove('hidden');
  document.getElementById('app-screen').classList.add('hidden');
  renderAuthForm();
}

function showApp() {
  document.getElementById('auth-screen').classList.add('hidden');
  document.getElementById('app-screen').classList.remove('hidden');
  renderSidebar();
  switchSection('home');
}

function renderAuthForm() {
  const card = document.getElementById('auth-card-body');
  const errEl = document.getElementById('auth-error');
  errEl.style.display = 'none';

  if (authMode === 'forgot') {
    card.innerHTML = `
      <div class="field">
        <label>Email address</label>
        <input class="input" id="auth-email" type="email" placeholder="you@example.com" />
      </div>
      <button class="btn btn-primary w-full" onclick="handleForgotPassword()">Send Reset Link</button>
      <div class="auth-switch">
        <a onclick="authMode='login'; renderAuthForm()">Back to login</a>
      </div>`;
    return;
  }

  let html = '';
  if (authMode === 'signup') {
    html += `<div class="field">
      <label>Full Name</label>
      <input class="input" id="auth-name" type="text" placeholder="John Doe" />
    </div>`;
  }
  html += `
    <div class="field">
      <label>Email address</label>
      <input class="input" id="auth-email" type="email" placeholder="you@example.com" />
    </div>
    <div class="field">
      <label>Password</label>
      <input class="input" id="auth-password" type="password" placeholder="${authMode === 'signup' ? 'Min 6 characters' : 'Your password'}" />
    </div>`;

  if (authMode === 'login') {
    html += `<a class="forgot-link" onclick="authMode='forgot'; renderAuthForm()">Forgot password?</a>`;
  }

  html += `<button class="btn btn-primary w-full" id="auth-submit-btn" onclick="handleEmailAuth()">${authMode === 'login' ? 'Sign In' : 'Create Account'}</button>`;
  html += `<div class="auth-divider">or</div>`;
  html += `<button class="btn-google" onclick="handleGoogleAuth()">
    <svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
    Continue with Google
  </button>`;

  html += `<div class="auth-switch">${authMode === 'login'
    ? 'Don\'t have an account? <a onclick="authMode=\'signup\'; renderAuthForm()">Sign up</a>'
    : 'Already have an account? <a onclick="authMode=\'login\'; renderAuthForm()">Sign in</a>'
  }</div>`;

  card.innerHTML = html;
  document.getElementById('auth-title').textContent = authMode === 'login' ? 'Welcome back' : 'Create your account';
}

function showAuthError(msg) {
  const el = document.getElementById('auth-error');
  el.textContent = msg;
  el.style.display = 'block';
}

async function handleEmailAuth() {
  const email = document.getElementById('auth-email')?.value.trim();
  const password = document.getElementById('auth-password')?.value;
  const name = document.getElementById('auth-name')?.value.trim();

  if (!email || !password) { showAuthError('Please fill in all fields'); return; }
  if (authMode === 'signup' && password.length < 6) { showAuthError('Password must be at least 6 characters'); return; }

  const btn = document.getElementById('auth-submit-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spin">&#10227;</span> Please wait...';

  try {
    if (authMode === 'signup') {
      const { data, error } = await sb.auth.signUp({
        email, password,
        options: {
          data: { full_name: name || '' },
          emailRedirectTo: 'https://the-study-website.onrender.com'
        }
      });
      if (error) throw error;
      if (data.user && !data.session) {
        showAuthError('');
        document.getElementById('auth-card-body').innerHTML = `
          <div class="empty" style="padding:20px">
            <div class="empty-icon">&#9993;</div>
            <h3>Check your email</h3>
            <p>We sent a confirmation link to <strong>${email}</strong>. Click it to activate your account.</p>
            <br>
            <a class="btn btn-secondary" onclick="authMode='login'; renderAuthForm()">Back to login</a>
          </div>`;
        return;
      }
    } else {
      const { error } = await sb.auth.signInWithPassword({ email, password });
      if (error) throw error;
    }
  } catch (err) {
    showAuthError(err.message || 'Authentication failed');
    btn.disabled = false;
    btn.innerHTML = authMode === 'login' ? 'Sign In' : 'Create Account';
  }
}

async function handleGoogleAuth() {
  const { error } = await sb.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: 'https://the-study-website.onrender.com' }
  });
  if (error) showAuthError(error.message);
}

async function handleForgotPassword() {
  const email = document.getElementById('auth-email')?.value.trim();
  if (!email) { showAuthError('Enter your email address'); return; }

  const { error } = await sb.auth.resetPasswordForEmail(email, {
    redirectTo: 'https://the-study-website.onrender.com'
  });
  if (error) { showAuthError(error.message); return; }

  document.getElementById('auth-card-body').innerHTML = `
    <div class="empty" style="padding:20px">
      <div class="empty-icon">&#9993;</div>
      <h3>Check your email</h3>
      <p>We sent a password reset link to <strong>${email}</strong>.</p>
      <br>
      <a class="btn btn-secondary" onclick="authMode='login'; renderAuthForm()">Back to login</a>
    </div>`;
}

async function handleLogout() {
  await sb.auth.signOut();
}

function toast(msg, type = 'info') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  const icons = { success: '&#10003;', error: '&#10005;', info: '&#8505;' };
  t.innerHTML = `<span>${icons[type]}</span><span>${msg}</span>`;
  document.getElementById('toast-container').appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

async function api(path, opts = {}) {
  const token = await getToken();
  const headers = { ...(opts.headers || {}), 'Authorization': 'Bearer ' + token };
  try {
    const r = await fetch(API + path, { ...opts, headers });
    const json = await r.json();
    if (!r.ok) throw new Error(json.error || `HTTP ${r.status}`);
    return json;
  } catch (e) {
    toast(e.message, 'error');
    throw e;
  }
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1048576).toFixed(1)} MB`;
}

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  return { pdf: '&#128213;', docx: '&#128216;', txt: '&#128196;', md: '&#128221;' }[ext] || '&#128196;';
}

function fileBg(name) {
  const ext = name.split('.').pop().toLowerCase();
  return { pdf: '#FEF2F2', docx: '#EFF6FF', txt: '#F0FDF4', md: '#FEFCE8' }[ext] || '#F8FAFC';
}

function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}

function renderMarkdown(text) {
  let s = text
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;');

  s = s.replace(/```([\w]*)\n?([\s\S]*?)```/g, (_,lang,code) =>
    `<pre class="md-pre"><code class="md-code">${code.trim()}</code></pre>`);

  s = s.replace(/`([^`\n]+)`/g,'<code class="md-inline-code">$1</code>');

  s = s.replace(/^#{4} (.+)$/gm,'<h4 class="md-h4">$1</h4>');
  s = s.replace(/^#{3} (.+)$/gm,'<h3 class="md-h3">$1</h3>');
  s = s.replace(/^#{2} (.+)$/gm,'<h2 class="md-h2">$1</h2>');
  s = s.replace(/^# (.+)$/gm,'<h1 class="md-h1">$1</h1>');

  s = s.replace(/\*\*\*(.+?)\*\*\*/g,'<strong><em>$1</em></strong>');
  s = s.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  s = s.replace(/\*([^\*\n]+?)\*/g,'<em>$1</em>');
  s = s.replace(/__(.+?)__/g,'<strong>$1</strong>');
  s = s.replace(/_([^_\n]+?)_/g,'<em>$1</em>');

  s = s.replace(/^---+$/gm,'<hr class="md-hr">');

  const lines = s.split('\n');
  const out = [];
  let inUl = false, inOl = false;
  for (const line of lines) {
    const ul = line.match(/^[\*\-] (.+)$/);
    const ol = line.match(/^\d+\.\s(.+)$/);
    if (ul) {
      if (inOl) { out.push('</ol>'); inOl = false; }
      if (!inUl) { out.push('<ul class="md-ul">'); inUl = true; }
      out.push(`<li>${ul[1]}</li>`);
    } else if (ol) {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (!inOl) { out.push('<ol class="md-ol">'); inOl = true; }
      out.push(`<li>${ol[1]}</li>`);
    } else {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
      out.push(line);
    }
  }
  if (inUl) out.push('</ul>');
  if (inOl) out.push('</ol>');

  s = out.join('\n');
  s = s.replace(/\n\n/g,'<br><br>');
  s = s.replace(/\n/g,'<br>');
  return s;
}

function getUserDisplayName() {
  if (userProfile?.full_name) return userProfile.full_name;
  if (currentUser?.user_metadata?.full_name) return currentUser.user_metadata.full_name;
  if (currentUser?.user_metadata?.name) return currentUser.user_metadata.name;
  if (currentUser?.email) return currentUser.email.split('@')[0];
  return 'User';
}

function getUserInitial() {
  const name = getUserDisplayName();
  return name.charAt(0).toUpperCase();
}

const state = {
  files: [],
  selectedFiles: new Set(),
  chatSessionId: null,
  chatMessages: [],
  savedOutputs: [],
};

let currentSection = 'home';

function renderSidebar() {
  const avatar = userProfile?.avatar_url
    ? `<img src="${userProfile.avatar_url}" alt="" />`
    : getUserInitial();

  document.getElementById('sidebar-user-area').innerHTML = `
    <div class="sidebar-user" onclick="switchSection('settings')" style="cursor:pointer" title="Edit profile">
      <div class="sidebar-avatar">${avatar}</div>
      <div style="min-width:0;flex:1">
        <div class="sidebar-username">${escapeHtml(getUserDisplayName())}</div>
        <div class="sidebar-email">${escapeHtml(currentUser?.email || '')}</div>
      </div>
      <span class="sidebar-settings-gear" title="Settings">&#9881;</span>
    </div>
    <button class="btn-logout" onclick="handleLogout()">Sign Out</button>`;
}

function switchSection(name) {
  currentSection = name;
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.section === name);
  });
  const titles = {
    home:       ['Home', 'Your study dashboard'],
    files:      ['Files', 'Upload and manage your study notes'],
    chat:       ['Chat', 'Ask questions about your notes'],
    output:     ['Output', 'Generate summaries, flashcards and quizzes'],
    cornell:    ['Cornell Notes', 'Generate structured notes using the Cornell method'],
    calendar:   ['Study Calendar', 'Plan your study sessions with Pomodoro'],
    playground: ['Playground', 'Run code and explore ideas freely'],
    settings:   ['Settings', 'Manage your profile and preferences'],
  };
  document.getElementById('topbar-title').textContent = titles[name][0];
  document.getElementById('topbar-sub').textContent = titles[name][1];
  document.getElementById('topbar-actions').innerHTML = '';

  const root = document.getElementById('content-root');
  root.innerHTML = '';

  const renders = {
    home: renderHome, files: renderFiles, chat: renderChat, output: renderOutput,
    cornell: renderCornell, calendar: renderCalendar, playground: renderPlayground,
    settings: renderSettings,
  };
  renders[name]();
}

async function renderHome() {
  const root = document.getElementById('content-root');
  const name = getUserDisplayName().split(' ')[0];
  const greetings = ['Hello', 'Hi', 'Hey', 'Salutations', 'Greetings', 'Welcome back', 'Hey there', 'Good to see you'];
  const initialGreeting = greetings[Math.floor(Math.random() * greetings.length)];

  root.innerHTML = `
    <div class="homepage">
      <div class="homepage-greeting"><span class="greeting-word" id="greeting-word">${initialGreeting}</span>, <span class="greeting-name">${escapeHtml(name)}</span></div>
      <div class="homepage-subtitle">What would you like to study today?</div>

      <div class="homepage-cards">
        <div class="homepage-card" onclick="switchSection('files')">
          <div class="hc-icon">&#128193;</div>
          <div class="hc-title">Upload Notes</div>
          <div class="hc-desc">Add PDF, DOCX, TXT, MD files</div>
        </div>
        <div class="homepage-card" onclick="switchSection('chat')">
          <div class="hc-icon">&#128172;</div>
          <div class="hc-title">Chat with AI</div>
          <div class="hc-desc">Ask questions about your notes</div>
        </div>
        <div class="homepage-card" onclick="switchSection('output')">
          <div class="hc-icon">&#128161;</div>
          <div class="hc-title">Generate Output</div>
          <div class="hc-desc">Summaries, flashcards, quizzes</div>
        </div>
        <div class="homepage-card" onclick="switchSection('cornell')">
          <div class="hc-icon">&#128221;</div>
          <div class="hc-title">Cornell Notes</div>
          <div class="hc-desc">Structured note generation</div>
        </div>
        <div class="homepage-card" onclick="switchSection('calendar')">
          <div class="hc-icon">&#128197;</div>
          <div class="hc-title">Study Calendar</div>
          <div class="hc-desc">Plan sessions & Pomodoro timer</div>
        </div>
        <div class="homepage-card" onclick="switchSection('playground')">
          <div class="hc-icon">&#9889;</div>
          <div class="hc-title">Playground</div>
          <div class="hc-desc">Run code & explore ideas</div>
        </div>
      </div>

      <div class="homepage-stats">
        <div class="stat-card">
          <div class="stat-num" id="stat-files">-</div>
          <div class="stat-label">Files Uploaded</div>
        </div>
        <div class="stat-card">
          <div class="stat-num" id="stat-outputs">-</div>
          <div class="stat-label">Outputs Generated</div>
        </div>
        <div class="stat-card">
          <div class="stat-num" id="stat-chats">-</div>
          <div class="stat-label">Chat Sessions</div>
        </div>
      </div>
    </div>`;

  try {
    const [files, outputs, sessions] = await Promise.all([
      api('/files'), api('/output'), api('/chat/sessions')
    ]);
    document.getElementById('stat-files').textContent = (files.files || []).length;
    document.getElementById('stat-outputs').textContent = (outputs.outputs || []).length;
    document.getElementById('stat-chats').textContent = (sessions.sessions || []).length;
    state.files = files.files || [];
    document.getElementById('badge-files').textContent = state.files.length;
  } catch {}
}


async function renderFiles() {
  const root = document.getElementById('content-root');
  root.innerHTML = `
    <div class="grid-2">
      <div>
        <div class="card mb-4">
          <div class="card-header"><span style="font-size:20px">&#128228;</span><span class="card-title">Upload Notes</span></div>
          <div class="card-body">
            <div class="upload-zone" id="drop-zone" onclick="document.getElementById('file-input').click()">
              <input type="file" id="file-input" accept=".pdf,.txt,.docx,.md" multiple />
              <div class="upload-icon">&#9729;</div>
              <h3>Drop files here or click to browse</h3>
              <p class="mt-1">Supports PDF, DOCX, TXT, MD - up to 16 MB</p>
            </div>
            <div class="progress-bar mt-3" id="upload-progress" style="display:none">
              <div class="progress-fill" id="upload-fill" style="width:0%"></div>
            </div>
          </div>
        </div>
      </div>
      <div>
        <div class="card">
          <div class="card-header">
            <span style="font-size:20px">&#128194;</span>
            <span class="card-title">Your Files</span>
            <span class="chip chip-gray" style="margin-left:auto" id="file-count">0 files</span>
          </div>
          <div class="card-body" id="file-list-body">
            <div class="empty"><div class="empty-icon">&#128237;</div><h3>No files yet</h3><p>Upload notes to get started</p></div>
          </div>
        </div>
      </div>
    </div>
    <div class="card mt-4" id="selection-info" style="display:none">
      <div class="card-body flex items-center justify-between">
        <span class="text-sm"><strong id="sel-count">0</strong> file(s) selected for AI context</span>
        <div class="flex gap-2">
          <button class="btn btn-secondary btn-sm" onclick="state.selectedFiles.clear(); renderFileList()">Clear</button>
          <button class="btn btn-primary btn-sm" onclick="switchSection('chat')">&#8594; Open in Chat</button>
          <button class="btn btn-primary btn-sm" onclick="switchSection('output')">&#8594; Generate Output</button>
        </div>
      </div>
    </div>`;
  setupDropZone();
  await loadFiles();
}

function setupDropZone() {
  const zone = document.getElementById('drop-zone');
  const input = document.getElementById('file-input');
  if (!zone) return;
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag'); handleUpload([...e.dataTransfer.files]); });
  input.addEventListener('change', () => handleUpload([...input.files]));
}

async function handleUpload(files) {
  const prog = document.getElementById('upload-progress');
  const fill = document.getElementById('upload-fill');
  if (!prog) return;
  prog.style.display = 'block';
  for (let i = 0; i < files.length; i++) {
    fill.style.width = `${Math.round(((i) / files.length) * 100)}%`;
    const fd = new FormData();
    fd.append('file', files[i]);
    try {
      await api('/files/upload', { method: 'POST', body: fd });
      toast(`Uploaded ${files[i].name}`, 'success');
    } catch {}
  }
  fill.style.width = '100%';
  setTimeout(() => { prog.style.display = 'none'; fill.style.width = '0%'; }, 600);
  await loadFiles();
}

async function loadFiles() {
  try {
    const data = await api('/files');
    state.files = data.files || [];
    renderFileList();
    const badge = document.getElementById('badge-files');
    if (badge) badge.textContent = state.files.length;
  } catch {}
}

function renderFileList() {
  const body = document.getElementById('file-list-body');
  const info = document.getElementById('selection-info');
  const selCount = document.getElementById('sel-count');
  if (!body) return;

  if (state.files.length === 0) {
    body.innerHTML = `<div class="empty"><div class="empty-icon">&#128237;</div><h3>No files yet</h3><p>Upload notes to get started</p></div>`;
    if (info) info.style.display = 'none';
    const fc = document.getElementById('file-count');
    if (fc) fc.textContent = '0 files';
    return;
  }

  const fc = document.getElementById('file-count');
  if (fc) fc.textContent = `${state.files.length} file${state.files.length > 1 ? 's' : ''}`;
  body.innerHTML = state.files.map(f => `
    <div class="file-item fade-in ${state.selectedFiles.has(f.id) ? 'selected' : ''}" id="fi-${f.id}" onclick="toggleFileSelect('${f.id}')">
      <div class="file-thumb" style="background:${fileBg(f.filename)}">${fileIcon(f.filename)}</div>
      <div class="file-info">
        <div class="file-name">${escapeHtml(f.filename)}</div>
        <div class="file-meta">${fmtSize(f.size)} &middot; ${fmtTime(f.uploaded_at)}</div>
      </div>
      <div class="file-actions">
        ${state.selectedFiles.has(f.id) ? '<span class="chip chip-blue" style="font-size:11px">&#10003; Selected</span>' : ''}
        <button class="btn btn-danger btn-sm" onclick="deleteFile(event,'${f.id}')">&#128465;</button>
      </div>
    </div>`).join('');

  if (info) {
    info.style.display = state.selectedFiles.size > 0 ? 'block' : 'none';
    if (selCount) selCount.textContent = state.selectedFiles.size;
  }
}

function toggleFileSelect(id) {
  if (state.selectedFiles.has(id)) state.selectedFiles.delete(id);
  else state.selectedFiles.add(id);
  renderFileList();
}

async function deleteFile(e, id) {
  e.stopPropagation();
  await api(`/files/${id}`, { method: 'DELETE' });
  state.selectedFiles.delete(id);
  toast('File deleted', 'success');
  await loadFiles();
}

async function renderChat() {
  const root = document.getElementById('content-root');
  root.innerHTML = `
    <div class="grid-2 mb-4" style="margin-bottom:16px">
      <div class="card">
        <div class="card-body">
          <div class="field" style="margin-bottom:0">
            <label>Attach notes to this chat</label>
            <div id="chat-file-chips" class="flex gap-2 mt-1" style="flex-wrap:wrap;min-height:28px"></div>
          </div>
        </div>
        <div class="card-footer">
          <button class="btn btn-primary w-full" id="btn-new-session" onclick="newChatSession(this)">&#10022; New Chat Session</button>
        </div>
      </div>
      <div class="card">
        <div class="card-body">
          <div class="text-sm text-muted mb-3">Session ID</div>
          <div style="font-family:monospace;font-size:12px;color:var(--text-2);word-break:break-all" id="session-id-display">${state.chatSessionId || '&#8212; no session yet &#8212;'}</div>
          <div class="mt-3">
            <button class="btn btn-danger btn-sm" onclick="clearChatHistory()" ${!state.chatSessionId ? 'disabled' : ''}>Clear History</button>
          </div>
        </div>
      </div>
    </div>
    <div class="card chat-wrap">
      <div class="chat-messages" id="chat-messages">
        <div class="empty"><div class="empty-icon">&#128172;</div><h3>Start a conversation</h3><p>Create a new session and ask anything about your notes</p></div>
      </div>
      <div class="chat-input-bar">
        <textarea id="chat-input" class="input" placeholder="Ask a question... (Enter to send, Shift+Enter for newline)" rows="1" onkeydown="chatKeydown(event)"></textarea>
        <button class="btn btn-primary" id="btn-send" onclick="sendChatMessage(this)" ${!state.chatSessionId ? 'disabled' : ''}>Send &#8593;</button>
      </div>
    </div>`;
  renderChatFileChips();
  if (state.chatMessages.length) renderChatMessages();
}

function renderChatFileChips() {
  const chips = document.getElementById('chat-file-chips');
  if (!chips) return;
  if (state.files.length === 0) {
    chips.innerHTML = `<span class="text-xs text-muted">No files uploaded yet</span>`;
    return;
  }
  chips.innerHTML = state.files.map(f => `
    <div class="chip ${state.selectedFiles.has(f.id) ? 'chip-blue' : 'chip-gray'}" style="cursor:pointer" onclick="toggleFileSelect('${f.id}'); renderChatFileChips()">
      ${fileIcon(f.filename)} ${f.filename.length > 18 ? f.filename.slice(0,15)+'...' : f.filename}
      ${state.selectedFiles.has(f.id) ? ' &#10003;' : ''}
    </div>`).join('');
}

async function newChatSession(btn) {
  btn.disabled = true; btn.innerHTML = '<span class="spin">&#10227;</span> Creating...';
  try {
    const fileIds = [...state.selectedFiles];
    const data = await api('/chat/session', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    });
    state.chatSessionId = data.session_id;
    state.chatMessages = [];
    toast('New chat session started', 'success');
    switchSection('chat');
  } catch { btn.disabled = false; btn.innerHTML = '&#10022; New Chat Session'; }
}

async function sendChatMessage(btn) {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg || !state.chatSessionId) return;
  input.value = ''; input.style.height = 'auto';
  state.chatMessages.push({ role: 'user', content: msg, created_at: new Date().toISOString() });
  renderChatMessages(); showTyping();
  btn.disabled = true;
  try {
    const data = await api(`/chat/${state.chatSessionId}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    });
    hideTyping();
    state.chatMessages.push({ role: 'assistant', content: data.reply, created_at: new Date().toISOString() });
    renderChatMessages();
  } catch { hideTyping(); } finally { btn.disabled = false; }
}

function chatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); document.getElementById('btn-send').click(); }
  e.target.style.height = 'auto';
  e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
}

function renderChatMessages() {
  const box = document.getElementById('chat-messages');
  if (!box) return;
  box.innerHTML = state.chatMessages.map(m => `
    <div class="msg ${m.role === 'user' ? 'user' : 'ai'} fade-in">
      <div class="msg-avatar">${m.role === 'user' ? '&#128100;' : '&#10022;'}</div>
      <div>
        <div class="msg-bubble${m.role !== 'user' ? ' md-prose' : ''}">${m.role === 'user' ? escapeHtml(m.content) : renderMarkdown(m.content)}</div>
        <div class="msg-time">${m.created_at ? fmtTime(m.created_at) : ''}</div>
      </div>
    </div>`).join('');
  box.scrollTop = box.scrollHeight;
}

function showTyping() {
  const box = document.getElementById('chat-messages');
  if (!box) return;
  const t = document.createElement('div');
  t.className = 'msg ai fade-in'; t.id = 'typing-msg';
  t.innerHTML = `<div class="msg-avatar">&#10022;</div><div class="msg-bubble"><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>`;
  box.appendChild(t); box.scrollTop = box.scrollHeight;
}

function hideTyping() { document.getElementById('typing-msg')?.remove(); }

async function clearChatHistory() {
  if (!state.chatSessionId) return;
  await api(`/chat/${state.chatSessionId}`, { method: 'DELETE' });
  state.chatMessages = [];
  renderChatMessages();
  toast('Chat history cleared', 'info');
}

const OUTPUT_TYPES = [
  { key: 'summary', icon: '&#128203;', label: 'Summary' },
  { key: 'flashcards', icon: '&#127183;', label: 'Flashcards' },
  { key: 'quiz', icon: '&#10067;', label: 'Quiz' },
  { key: 'key_points', icon: '&#128273;', label: 'Key Points' },
  { key: 'explain', icon: '&#128161;', label: 'Explain' },
  { key: 'custom', icon: '&#9999;&#65039;', label: 'Custom' },
];

let selectedOutputType = 'summary';

async function renderOutput() {
  const root = document.getElementById('content-root');
  root.innerHTML = `
    <div class="grid-2">
      <div>
        <div class="card mb-4">
          <div class="card-header"><span style="font-size:20px">&#9881;&#65039;</span><span class="card-title">Generate</span></div>
          <div class="card-body">
            <div class="field">
              <label>Select notes</label>
              <div id="out-file-list">
                ${state.files.length === 0
                  ? '<p class="text-sm text-muted">No files uploaded yet</p>'
                  : state.files.map(f => `
                    <div class="file-item ${state.selectedFiles.has(f.id) ? 'selected' : ''}" style="margin-bottom:6px;cursor:pointer" id="ofi-${f.id}" onclick="toggleOutputFile('${f.id}')">
                      <div class="file-thumb" style="background:${fileBg(f.filename)};font-size:14px">${fileIcon(f.filename)}</div>
                      <div class="file-info"><div class="file-name" style="font-size:13px">${escapeHtml(f.filename)}</div></div>
                      ${state.selectedFiles.has(f.id) ? '<span class="chip chip-blue" style="font-size:11px">&#10003;</span>' : ''}
                    </div>`).join('')}
              </div>
            </div>
            <div class="field">
              <label>Output type</label>
              <div class="output-type-grid" id="type-grid">
                ${OUTPUT_TYPES.map(t => `
                  <div class="type-card ${t.key === selectedOutputType ? 'selected' : ''}" onclick="selectOutputType('${t.key}', this)">
                    <div class="type-icon">${t.icon}</div>
                    <div class="type-label">${t.label}</div>
                  </div>`).join('')}
              </div>
            </div>
            <div class="field" id="custom-prompt-field" style="display:${selectedOutputType === 'custom' ? 'block' : 'none'}">
              <label>Custom prompt</label>
              <textarea class="input" id="custom-prompt" placeholder="e.g. Create a mind map outline..."></textarea>
            </div>
            <button class="btn btn-primary w-full" id="btn-generate" onclick="generateOutput(this)">&#9889; Generate</button>
          </div>
        </div>
      </div>
      <div>
        <div class="card">
          <div class="card-header">
            <span style="font-size:20px">&#128196;</span>
            <span class="card-title">Result</span>
            <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="copyOutput()" id="btn-copy" disabled>&#128203; Copy</button>
          </div>
          <div class="card-body" id="output-result-body">
            <div class="empty"><div class="empty-icon">&#128196;</div><h3>Nothing generated yet</h3><p>Select files, choose a type, and click Generate</p></div>
          </div>
        </div>
        <div class="card mt-4">
          <div class="card-header"><span style="font-size:20px">&#128450;</span><span class="card-title">Saved Outputs</span></div>
          <div class="card-body" id="saved-outputs-list">
            <div class="empty"><div class="empty-icon">&#128450;</div><h3>No saved outputs</h3></div>
          </div>
        </div>
      </div>
    </div>`;
  loadSavedOutputs();
}

function toggleOutputFile(id) {
  if (state.selectedFiles.has(id)) state.selectedFiles.delete(id);
  else state.selectedFiles.add(id);
  state.files.forEach(f => {
    const el = document.getElementById(`ofi-${f.id}`);
    if (!el) return;
    el.className = `file-item ${state.selectedFiles.has(f.id) ? 'selected' : ''} fade-in`;
    el.style.marginBottom = '6px'; el.style.cursor = 'pointer';
  });
}

function selectOutputType(key, el) {
  selectedOutputType = key;
  document.querySelectorAll('.type-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  const cpf = document.getElementById('custom-prompt-field');
  if (cpf) cpf.style.display = key === 'custom' ? 'block' : 'none';
}

async function generateOutput(btn) {
  const fileIds = [...state.selectedFiles];
  if (fileIds.length === 0) { toast('Select at least one file', 'error'); return; }
  const customPrompt = selectedOutputType === 'custom' ? (document.getElementById('custom-prompt')?.value.trim() || '') : '';
  if (selectedOutputType === 'custom' && !customPrompt) { toast('Enter a custom prompt', 'error'); return; }

  const origLabel = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spin">&#10227;</span> Generating...';
  document.getElementById('output-result-body').innerHTML = `<div class="empty"><div class="empty-icon" style="animation:spin 1s linear infinite">&#10227;</div><h3>Generating...</h3></div>`;

  try {
    const data = await api('/output/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds, type: selectedOutputType, custom_prompt: customPrompt }),
    });
    if (selectedOutputType === 'quiz') renderInteractiveQuiz(data.content);
    else if (selectedOutputType === 'flashcards') renderInteractiveFlashcards(data.content);
    else document.getElementById('output-result-body').innerHTML = `<div class="output-result md-prose fade-in">${renderMarkdown(data.content)}</div>`;
    document.getElementById('btn-copy').disabled = false;
    document.getElementById('btn-copy').dataset.content = data.content;
    toast('Output generated!', 'success');
    loadSavedOutputs();
  } catch {} finally { btn.disabled = false; btn.innerHTML = origLabel; }
}

function copyOutput() {
  const content = document.getElementById('btn-copy').dataset.content;
  if (!content) return;
  navigator.clipboard.writeText(content).then(() => toast('Copied to clipboard', 'success'));
}

async function loadSavedOutputs() {
  try {
    const data = await api('/output');
    state.savedOutputs = data.outputs || [];
    const box = document.getElementById('saved-outputs-list');
    if (!box) return;
    if (state.savedOutputs.length === 0) {
      box.innerHTML = `<div class="empty" style="padding:20px"><div class="empty-icon">&#128450;</div><h3>No saved outputs</h3></div>`;
      return;
    }
    const typeIcons = { summary:'&#128203;', flashcards:'&#127183;', quiz:'&#10067;', key_points:'&#128273;', explain:'&#128161;', custom:'&#9999;', cornell:'&#128221;' };
    box.innerHTML = state.savedOutputs.map(o => `
      <div class="output-list-item fade-in" onclick="viewOutput('${o.output_id}')">
        <span style="font-size:20px">${typeIcons[o.type] || '&#128196;'}</span>
        <div style="flex:1;min-width:0">
          <div class="font-semibold text-sm">${o.type.replace('_',' ')}</div>
          <div class="text-xs text-muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(o.preview)}</div>
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteOutput(event,'${o.output_id}')">&#128465;</button>
      </div>`).join('');
  } catch {}
}

async function viewOutput(id) {
  try {
    const data = await api(`/output/${id}`);
    const box = document.getElementById('output-result-body');
    if (box) {
      if (data.type === 'quiz') renderInteractiveQuiz(data.content);
      else if (data.type === 'flashcards') renderInteractiveFlashcards(data.content);
      else box.innerHTML = `<div class="output-result md-prose fade-in">${renderMarkdown(data.content)}</div>`;
      document.getElementById('btn-copy').disabled = false;
      document.getElementById('btn-copy').dataset.content = data.content;
    }
  } catch {}
}

async function deleteOutput(e, id) {
  e.stopPropagation();
  await api(`/output/${id}`, { method: 'DELETE' });
  toast('Output deleted', 'info');
  loadSavedOutputs();
}

let fcData = [], fcIndex = 0, fcFlipped = false;

function renderInteractiveFlashcards(content) {
  const box = document.getElementById('output-result-body');
  try {
    let raw = content.trim();
    if (raw.startsWith('```')) raw = raw.replace(/^```(?:json)?\s*/,'').replace(/```\s*$/,'');
    fcData = JSON.parse(raw); fcIndex = 0; fcFlipped = false;
    renderFlashcardUI();
  } catch { box.innerHTML = `<div class="output-result fade-in">${escapeHtml(content)}</div>`; }
}

function renderFlashcardUI() {
  const box = document.getElementById('output-result-body');
  if (!fcData.length) return;
  const card = fcData[fcIndex];
  box.innerHTML = `
    <div class="fc-container fade-in">
      <div class="fc-card ${fcFlipped ? 'flipped' : ''}" onclick="flipCard()">
        <div class="fc-inner">
          <div class="fc-front"><span class="fc-label">Question</span><div>${escapeHtml(card.front)}</div><div class="fc-hint">Click to reveal answer</div></div>
          <div class="fc-back"><span class="fc-label">Answer</span><div>${escapeHtml(card.back)}</div><div class="fc-hint">Click to see question</div></div>
        </div>
      </div>
      <div class="fc-nav">
        <button class="btn btn-ghost" onclick="fcPrev()" ${fcIndex === 0 ? 'disabled' : ''}>&#9664;</button>
        <span class="fc-counter">${fcIndex + 1} / ${fcData.length}</span>
        <button class="btn btn-ghost" onclick="fcNext()" ${fcIndex === fcData.length - 1 ? 'disabled' : ''}>&#9654;</button>
      </div>
    </div>`;
}

function flipCard() { fcFlipped = !fcFlipped; renderFlashcardUI(); }
function fcPrev() { if (fcIndex > 0) { fcIndex--; fcFlipped = false; renderFlashcardUI(); } }
function fcNext() { if (fcIndex < fcData.length - 1) { fcIndex++; fcFlipped = false; renderFlashcardUI(); } }

let quizData = [], quizAnswers = {}, quizSubmitted = false;

function renderInteractiveQuiz(content) {
  const box = document.getElementById('output-result-body');
  try {
    let raw = content.trim();
    if (raw.startsWith('```')) raw = raw.replace(/^```(?:json)?\s*/,'').replace(/```\s*$/,'');
    quizData = JSON.parse(raw); quizAnswers = {}; quizSubmitted = false;
    renderQuizUI();
  } catch { box.innerHTML = `<div class="output-result fade-in">${escapeHtml(content)}</div>`; }
}

function renderQuizUI() {
  const box = document.getElementById('output-result-body');
  const total = quizData.length;
  const answered = Object.keys(quizAnswers).length;
  let html = `<div class="quiz-container fade-in"><div class="quiz-progress">${answered} of ${total} answered</div>`;
  quizData.forEach((q, qi) => {
    html += `<div class="quiz-question"><div class="quiz-question-text">${qi + 1}. ${escapeHtml(q.q)}</div>`;
    q.options.forEach((opt, oi) => {
      let cls = 'quiz-option';
      if (quizSubmitted) { if (oi === q.answer) cls += ' correct'; else if (quizAnswers[qi] === oi) cls += ' incorrect'; }
      else if (quizAnswers[qi] === oi) cls += ' selected';
      html += `<button class="${cls}" ${quizSubmitted ? 'disabled' : ''} onclick="selectQuizAnswer(${qi},${oi})">${escapeHtml(opt)}</button>`;
    });
    html += `</div>`;
  });
  if (!quizSubmitted) html += `<button class="btn btn-primary w-full quiz-submit" onclick="submitQuiz()" ${answered < total ? 'disabled' : ''}>Submit Quiz</button>`;
  else {
    const correct = quizData.filter((q, i) => quizAnswers[i] === q.answer).length;
    html += `<div class="quiz-score"><div class="score-num">${correct}/${total}</div><div>${correct === total ? 'Perfect score!' : correct >= total * 0.7 ? 'Good job!' : 'Keep studying!'}</div></div>`;
    html += `<button class="btn btn-primary w-full quiz-submit" onclick="retakeQuiz()">Retake Quiz</button>`;
  }
  html += `</div>`;
  box.innerHTML = html;
}

function selectQuizAnswer(qi, oi) {
  if (quizSubmitted) return;
  quizAnswers[qi] = oi;
  const qEl = document.querySelectorAll('.quiz-question')[qi];
  if (qEl) qEl.querySelectorAll('.quiz-option').forEach((btn, i) => { btn.className = 'quiz-option' + (i === oi ? ' selected' : ''); });
  const total = quizData.length; const answered = Object.keys(quizAnswers).length;
  const prog = document.querySelector('.quiz-progress');
  if (prog) prog.textContent = `${answered} of ${total} answered`;
  const sub = document.querySelector('.quiz-submit');
  if (sub && !quizSubmitted) sub.disabled = answered < total;
}

function submitQuiz() { quizSubmitted = true; renderQuizUI(); }
function retakeQuiz() { quizAnswers = {}; quizSubmitted = false; renderQuizUI(); }

async function renderCornell() {
  const root = document.getElementById('content-root');
  root.innerHTML = `
    <div class="grid-2">
      <div>
        <div class="card mb-4">
          <div class="card-header"><span style="font-size:20px">&#128221;</span><span class="card-title">Cornell Notes Generator</span></div>
          <div class="card-body">
            <div class="field">
              <label>Select notes</label>
              <div id="cornell-file-list">
                ${state.files.length === 0
                  ? '<p class="text-sm text-muted">No files uploaded yet</p>'
                  : state.files.map(f => `
                    <div class="file-item ${state.selectedFiles.has(f.id) ? 'selected' : ''}" style="margin-bottom:6px;cursor:pointer" id="cfi-${f.id}" onclick="toggleCornellFile('${f.id}')">
                      <div class="file-thumb" style="background:${fileBg(f.filename)};font-size:14px">${fileIcon(f.filename)}</div>
                      <div class="file-info"><div class="file-name" style="font-size:13px">${escapeHtml(f.filename)}</div></div>
                      ${state.selectedFiles.has(f.id) ? '<span class="chip chip-blue" style="font-size:11px">&#10003;</span>' : ''}
                    </div>`).join('')}
              </div>
            </div>
            <button class="btn btn-primary w-full" id="btn-cornell" onclick="generateCornell(this)">&#128221; Generate Cornell Notes</button>
          </div>
        </div>
      </div>
      <div>
        <div class="card">
          <div class="card-header">
            <span style="font-size:20px">&#128221;</span><span class="card-title">Cornell Notes</span>
            <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="copyCornell()" id="btn-cornell-copy" disabled>&#128203; Copy</button>
          </div>
          <div class="card-body" id="cornell-result-body">
            <div class="empty"><div class="empty-icon">&#128221;</div><h3>Nothing generated yet</h3><p>Select files and click Generate Cornell Notes</p></div>
          </div>
        </div>
      </div>
    </div>`;
}

function toggleCornellFile(id) {
  if (state.selectedFiles.has(id)) state.selectedFiles.delete(id);
  else state.selectedFiles.add(id);
  state.files.forEach(f => {
    const el = document.getElementById(`cfi-${f.id}`);
    if (!el) return;
    el.className = `file-item ${state.selectedFiles.has(f.id) ? 'selected' : ''} fade-in`;
    el.style.marginBottom = '6px'; el.style.cursor = 'pointer';
  });
}

let lastCornellText = '';

async function generateCornell(btn) {
  const fileIds = [...state.selectedFiles];
  if (fileIds.length === 0) { toast('Select at least one file', 'error'); return; }
  const origLabel = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spin">&#10227;</span> Generating...';
  document.getElementById('cornell-result-body').innerHTML = `<div class="empty"><div class="empty-icon" style="animation:spin 1s linear infinite">&#10227;</div><h3>Generating Cornell Notes...</h3></div>`;

  try {
    const data = await api('/cornell/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    });
    const body = document.getElementById('cornell-result-body');
    if (data.cornell) {
      const c = data.cornell;
      const today = new Date().toLocaleDateString();
      let cuesHtml = '', notesHtml = '';
      for (let i = 0; i < c.cues.length; i++) {
        cuesHtml += `<div class="cornell-cue-item">${escapeHtml(c.cues[i])}</div>`;
        notesHtml += `<div class="cornell-note-item">${escapeHtml(c.notes[i])}</div>`;
      }
      body.innerHTML = `
        <div class="cornell-page fade-in">
          <div class="cornell-header"><span>${escapeHtml(c.title)}</span><span class="cornell-date">${today}</span></div>
          <div class="cornell-body"><div class="cornell-cue-col">${cuesHtml}</div><div class="cornell-notes-col">${notesHtml}</div></div>
          <div class="cornell-summary"><div class="cornell-summary-label">Summary</div><div>${escapeHtml(c.summary)}</div></div>
        </div>`;
      lastCornellText = `CORNELL NOTES: ${c.title}\nDate: ${today}\n\n`;
      for (let i = 0; i < c.cues.length; i++) lastCornellText += `[CUE] ${c.cues[i]}\n${c.notes[i]}\n\n`;
      lastCornellText += `---\nSUMMARY\n${c.summary}`;
      document.getElementById('btn-cornell-copy').disabled = false;
    } else if (data.raw) {
      body.innerHTML = `<div class="output-result fade-in">${escapeHtml(data.raw)}</div>`;
      lastCornellText = data.raw;
      document.getElementById('btn-cornell-copy').disabled = false;
    }
    toast('Cornell Notes generated!', 'success');
  } catch {} finally { btn.disabled = false; btn.innerHTML = origLabel; }
}

function copyCornell() {
  if (!lastCornellText) return;
  navigator.clipboard.writeText(lastCornellText).then(() => toast('Copied to clipboard', 'success'));
}

async function renderPlayground() {
  const root = document.getElementById('content-root');
  root.innerHTML = `
    <div class="card mb-4">
      <div class="card-header">
        <span style="font-size:20px">&#9889;</span><span class="card-title">Code Runner</span>
        <span class="chip chip-gray" style="margin-left:8px">Python</span>
        <div style="margin-left:auto;display:flex;gap:8px">
          <button class="btn btn-secondary btn-sm" id="btn-explain" onclick="explainCode(this)">&#128161; Explain</button>
          <button class="btn btn-primary btn-sm" id="btn-run" onclick="runCode(this)">&#9654; Run</button>
        </div>
      </div>
      <div class="playground-grid" style="padding:16px;height:320px">
        <textarea class="code-editor" id="code-editor" placeholder="# Write your Python here...">def fib(n):
    a, b = 0, 1
    for _ in range(n):
        print(a, end=' ')
        a, b = b, a + b

fib(10)</textarea>
        <div class="run-output" id="run-output"><span style="color:#475569">// Output will appear here after you hit Run</span></div>
      </div>
    </div>
    <div class="card mb-4">
      <div class="card-header"><span style="font-size:20px">&#129302;</span><span class="card-title">Ask AI Anything</span></div>
      <div class="card-body">
        <div class="field mb-3">
          <label>Attach notes (optional)</label>
          <div id="pg-file-chips" class="flex gap-2" style="flex-wrap:wrap;margin-top:6px"></div>
        </div>
        <div class="flex gap-2">
          <textarea class="input" id="pg-prompt" placeholder="Ask anything - explain a concept, debug code, explore ideas..." rows="3" style="flex:1;resize:none"></textarea>
          <button class="btn btn-primary" id="btn-pg-ask" onclick="pgAsk(this)" style="align-self:flex-end">Ask &#8593;</button>
        </div>
        <div id="pg-response" style="display:none" class="pg-response fade-in"></div>
      </div>
    </div>
    <div class="card" id="explain-card" style="display:none">
      <div class="card-header"><span style="font-size:20px">&#128161;</span><span class="card-title">Code Explanation</span></div>
      <div class="card-body"><div class="pg-response" id="explain-result" style="max-height:400px"></div></div>
    </div>`;
  renderPgFileChips();
}

function renderPgFileChips() {
  const chips = document.getElementById('pg-file-chips');
  if (!chips) return;
  if (state.files.length === 0) { chips.innerHTML = `<span class="text-xs text-muted">No files uploaded</span>`; return; }
  chips.innerHTML = state.files.map(f => `
    <div class="chip ${state.selectedFiles.has(f.id) ? 'chip-blue' : 'chip-gray'}" style="cursor:pointer" onclick="toggleFileSelect('${f.id}'); renderPgFileChips()">
      ${fileIcon(f.filename)} ${f.filename.length > 18 ? f.filename.slice(0,15)+'...' : f.filename}
    </div>`).join('');
}

async function runCode(btn) {
  const code = document.getElementById('code-editor').value;
  if (!code.trim()) { toast('Write some code first', 'error'); return; }
  const out = document.getElementById('run-output');
  const origLabel = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spin">&#10227;</span>';
  out.innerHTML = `<span style="color:#475569">Running...</span>`;
  try {
    const data = await api('/playground/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) });
    let html = '';
    if (data.stdout) html += escapeHtml(data.stdout);
    if (data.stderr) html += `<span class="stderr">${escapeHtml(data.stderr)}</span>`;
    if (!data.stdout && !data.stderr) html = '<span style="color:#475569">No output</span>';
    html += `<div class="exit-code">Exit code: ${data.exit_code}</div>`;
    out.innerHTML = html;
  } catch { out.innerHTML = '<span class="stderr">Error executing code</span>'; }
  finally { btn.disabled = false; btn.innerHTML = origLabel; }
}

async function explainCode(btn) {
  const code = document.getElementById('code-editor').value;
  if (!code.trim()) { toast('Write some code first', 'error'); return; }
  const origLabel = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spin">&#10227;</span>';
  try {
    const data = await api('/playground/explain', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, language: 'Python' }) });
    const card = document.getElementById('explain-card');
    const result = document.getElementById('explain-result');
    if (card && result) { card.style.display = 'block'; result.classList.add('md-prose'); result.innerHTML = renderMarkdown(data.explanation); card.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
    toast('Explanation ready', 'success');
  } catch {} finally { btn.disabled = false; btn.innerHTML = origLabel; }
}

async function pgAsk(btn) {
  const prompt = document.getElementById('pg-prompt').value.trim();
  if (!prompt) { toast('Enter a prompt', 'error'); return; }
  const origLabel = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spin">&#10227;</span>';
  const respBox = document.getElementById('pg-response');
  respBox.style.display = 'block'; respBox.innerHTML = '<span style="color:var(--text-3)">Thinking...</span>';
  try {
    const data = await api('/playground/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt, file_ids: [...state.selectedFiles] }) });
    respBox.classList.add('md-prose'); respBox.innerHTML = renderMarkdown(data.response);
  } catch { respBox.innerHTML = '<span style="color:var(--red)">Something went wrong</span>'; }
  finally { btn.disabled = false; btn.innerHTML = origLabel; }
}

const CAL_COLORS = [
  { name:'Blue', bg:'#DBEAFE', fg:'#1E40AF', dot:'#2563EB' },
  { name:'Red', bg:'#FEE2E2', fg:'#991B1B', dot:'#EF4444' },
  { name:'Green', bg:'#DCFCE7', fg:'#166534', dot:'#22C55E' },
  { name:'Purple', bg:'#F3E8FF', fg:'#6B21A8', dot:'#A855F7' },
  { name:'Orange', bg:'#FFEDD5', fg:'#9A3412', dot:'#F97316' },
  { name:'Teal', bg:'#CCFBF1', fg:'#115E59', dot:'#14B8A6' },
  { name:'Pink', bg:'#FCE7F3', fg:'#9D174D', dot:'#EC4899' },
  { name:'Yellow', bg:'#FEF9C3', fg:'#854D0E', dot:'#EAB308' },
];

const STUDY_METHODS = [
  { key:'pomodoro', label:'Pomodoro', desc:'25 min work / 5 min break' },
  { key:'pomodoro_long', label:'Long Pomodoro', desc:'50 min work / 10 min break' },
  { key:'freeform', label:'Free Study', desc:'Continuous study session' },
  { key:'active_recall', label:'Active Recall', desc:'Test yourself from memory' },
  { key:'spaced_rep', label:'Spaced Repetition', desc:'Review at intervals' },
];

const cal = { events: JSON.parse(localStorage.getItem('studycal_events') || '[]'), view: 'month', viewDate: new Date(), selectedDate: null };

function saveCalEvents() { localStorage.setItem('studycal_events', JSON.stringify(cal.events)); }
function calDateKey(d) { return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }
function getEventsForDate(dateKey) { return cal.events.filter(e => e.date === dateKey).sort((a,b) => a.startTime.localeCompare(b.startTime)); }

function renderCalendar() {
  const root = document.getElementById('content-root');
  root.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 340px;gap:20px;align-items:start">
      <div>
        <div class="cal-toolbar">
          <button class="btn btn-secondary btn-sm" onclick="calToday()">Today</button>
          <button class="btn btn-ghost btn-sm" onclick="calNav(-1)">&#9664;</button>
          <button class="btn btn-ghost btn-sm" onclick="calNav(1)">&#9654;</button>
          <span class="cal-title" id="cal-title"></span>
          <div style="margin-left:auto">
            <div class="cal-view-tabs">
              <button class="${cal.view==='month'?'active':''}" onclick="calSetView('month')">Month</button>
              <button class="${cal.view==='week'?'active':''}" onclick="calSetView('week')">Week</button>
              <button class="${cal.view==='day'?'active':''}" onclick="calSetView('day')">Day</button>
            </div>
          </div>
        </div>
        <div id="cal-grid"></div>
      </div>
      <div>
        <div class="pomo-widget mb-4">
          <div class="pomo-header"><span style="font-size:18px">&#127813;</span><span class="card-title">Pomodoro Timer</span></div>
          <div class="pomo-body">
            <svg class="pomo-progress-ring" width="180" height="180" viewBox="0 0 180 180">
              <circle cx="90" cy="90" r="82" fill="none" stroke="var(--border)" stroke-width="6"/>
              <circle id="pomo-ring" cx="90" cy="90" r="82" fill="none" stroke="var(--blue)" stroke-width="6" stroke-linecap="round" stroke-dasharray="${2*Math.PI*82}" stroke-dashoffset="0" transform="rotate(-90 90 90)"/>
            </svg>
            <div class="pomo-time" id="pomo-time">25:00</div>
            <div class="pomo-phase" id="pomo-phase">Focus Time</div>
            <div class="pomo-session-info" id="pomo-session-info">Session 1 of 4</div>
            <div class="pomo-controls">
              <button class="btn btn-primary" id="pomo-start" onclick="pomoToggle()">&#9654; Start</button>
              <button class="btn btn-secondary" onclick="pomoReset()">&#8634; Reset</button>
              <button class="btn btn-ghost" onclick="pomoSkip()">&#9197; Skip</button>
            </div>
          </div>
          <div class="pomo-settings">
            <div class="pomo-settings-grid">
              <div><label>Focus (min)</label><input type="number" id="pomo-focus-min" value="25" min="1" max="90" onchange="pomoUpdateSettings()"></div>
              <div><label>Break (min)</label><input type="number" id="pomo-break-min" value="5" min="1" max="30" onchange="pomoUpdateSettings()"></div>
              <div><label>Sessions</label><input type="number" id="pomo-sessions" value="4" min="1" max="10" onchange="pomoUpdateSettings()"></div>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-header"><span style="font-size:18px">&#128203;</span><span class="card-title">Today's Schedule</span></div>
          <div class="card-body" id="cal-today-list"></div>
        </div>
      </div>
    </div>`;
  calRenderView(); calRenderTodayList(); pomoRenderTime();
}

function calRenderView() { if (cal.view === 'month') calRenderMonth(); else if (cal.view === 'week') calRenderWeek(); else calRenderDay(); }
function calSetView(v) { cal.view = v; renderCalendar(); }
function calToday() { cal.viewDate = new Date(); cal.selectedDate = null; renderCalendar(); }
function calNav(dir) { const d = cal.viewDate; if (cal.view === 'month') d.setMonth(d.getMonth() + dir); else if (cal.view === 'week') d.setDate(d.getDate() + dir * 7); else d.setDate(d.getDate() + dir); renderCalendar(); }

function calRenderMonth() {
  const d = cal.viewDate; const year = d.getFullYear(), month = d.getMonth();
  document.getElementById('cal-title').textContent = d.toLocaleDateString('en-US', { month:'long', year:'numeric' });
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month+1, 0).getDate();
  const daysInPrev = new Date(year, month, 0).getDate();
  const today = calDateKey(new Date());
  let html = '<div class="cal-month-grid">';
  ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].forEach(d => html += `<div class="cal-day-header">${d}</div>`);
  const totalCells = Math.ceil((firstDay + daysInMonth) / 7) * 7;
  for (let i = 0; i < totalCells; i++) {
    let day, dateObj, otherMonth = false;
    if (i < firstDay) { day = daysInPrev - firstDay + i + 1; dateObj = new Date(year, month-1, day); otherMonth = true; }
    else if (i >= firstDay + daysInMonth) { day = i - firstDay - daysInMonth + 1; dateObj = new Date(year, month+1, day); otherMonth = true; }
    else { day = i - firstDay + 1; dateObj = new Date(year, month, day); }
    const dk = calDateKey(dateObj); const isToday = dk === today; const events = getEventsForDate(dk);
    html += `<div class="cal-day-cell ${otherMonth?'other-month':''} ${isToday?'today':''}" onclick="calDayClick('${dk}')">`;
    html += `<div class="cal-day-num">${day}</div>`;
    events.slice(0, 3).forEach(ev => { const c = CAL_COLORS[ev.color || 0]; html += `<div class="cal-event" style="background:${c.bg};color:${c.fg}" onclick="event.stopPropagation();calEditEvent('${ev.id}')" title="${ev.subject}">${ev.startTime} ${escapeHtml(ev.subject)}</div>`; });
    if (events.length > 3) html += `<div class="cal-event" style="background:var(--surface2);color:var(--text-2)">+${events.length-3} more</div>`;
    html += `</div>`;
  }
  html += '</div>';
  document.getElementById('cal-grid').innerHTML = html;
}

function calRenderWeek() {
  const d = new Date(cal.viewDate); const day = d.getDay();
  const weekStart = new Date(d); weekStart.setDate(d.getDate() - day);
  const today = calDateKey(new Date());
  const weekEnd = new Date(weekStart); weekEnd.setDate(weekStart.getDate() + 6);
  document.getElementById('cal-title').textContent = `${weekStart.toLocaleDateString('en-US',{month:'short',day:'numeric'})} - ${weekEnd.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}`;
  let html = '<div class="cal-week-grid"><div class="cal-week-header-row"><div class="cal-week-header-cell"></div>';
  const dayNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  for (let i = 0; i < 7; i++) { const dd = new Date(weekStart); dd.setDate(weekStart.getDate()+i); const dk = calDateKey(dd); const isToday = dk === today; html += `<div class="cal-week-header-cell ${isToday?'today-col':''}"><div>${dayNames[i]}</div><div class="cal-week-daynum">${dd.getDate()}</div></div>`; }
  html += '</div><div class="cal-week-body">';
  for (let h = 6; h < 23; h++) {
    const hLabel = h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h-12} PM`;
    html += `<div class="cal-hour-label">${hLabel}</div>`;
    for (let i = 0; i < 7; i++) { const dd = new Date(weekStart); dd.setDate(weekStart.getDate()+i); const dk = calDateKey(dd);
      const events = getEventsForDate(dk).filter(e => parseInt(e.startTime.split(':')[0]) === h);
      html += `<div class="cal-week-col-cell" onclick="calDayClick('${dk}','${String(h).padStart(2,'0')}:00')">`;
      events.forEach(ev => { const c = CAL_COLORS[ev.color || 0]; const durH = Math.max(1, Math.round((ev.duration || 25) / 60)); html += `<div class="cal-week-event" style="background:${c.bg};color:${c.fg};height:${durH*46}px" onclick="event.stopPropagation();calEditEvent('${ev.id}')">${escapeHtml(ev.subject)}</div>`; });
      html += '</div>';
    }
  }
  html += '</div></div>';
  document.getElementById('cal-grid').innerHTML = html;
}

function calRenderDay() {
  const d = cal.viewDate; const dk = calDateKey(d); const today = calDateKey(new Date());
  document.getElementById('cal-title').textContent = d.toLocaleDateString('en-US', { weekday:'long', month:'long', day:'numeric', year:'numeric' });
  let html = '<div class="cal-day-view">';
  html += `<div class="cal-day-view-header">${d.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'})} ${dk===today?'<span class="chip chip-blue" style="margin-left:8px">Today</span>':''}</div>`;
  html += '<div class="cal-day-timeline">';
  for (let h = 6; h < 23; h++) {
    const hLabel = h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h-12} PM`;
    const events = getEventsForDate(dk).filter(e => parseInt(e.startTime.split(':')[0]) === h);
    html += `<div class="cal-day-row" onclick="calDayClick('${dk}','${String(h).padStart(2,'0')}:00')"><div class="cal-day-hour">${hLabel}</div><div class="cal-day-events">`;
    events.forEach(ev => { const c = CAL_COLORS[ev.color || 0]; const method = STUDY_METHODS.find(m => m.key === ev.method);
      html += `<div class="cal-day-evt" style="background:${c.bg};color:${c.fg}" onclick="event.stopPropagation();calEditEvent('${ev.id}')"><span class="evt-time">${ev.startTime}</span><strong>${escapeHtml(ev.subject)}</strong><span>${ev.duration} min</span>${method ? `<span class="evt-method">${method.label}</span>` : ''}</div>`; });
    html += '</div></div>';
  }
  html += '</div></div>';
  document.getElementById('cal-grid').innerHTML = html;
}

function calRenderTodayList() {
  const box = document.getElementById('cal-today-list'); if (!box) return;
  const today = calDateKey(new Date()); const events = getEventsForDate(today);
  if (events.length === 0) { box.innerHTML = '<div class="empty" style="padding:16px"><div class="empty-icon">&#128197;</div><h3>No sessions today</h3><p>Click on the calendar to add one</p></div>'; return; }
  box.innerHTML = events.map(ev => { const c = CAL_COLORS[ev.color || 0]; const method = STUDY_METHODS.find(m => m.key === ev.method);
    return `<div class="file-item fade-in" style="cursor:pointer" onclick="calEditEvent('${ev.id}')"><div style="width:4px;height:36px;border-radius:4px;background:${c.dot};flex-shrink:0"></div><div class="file-info"><div class="file-name">${escapeHtml(ev.subject)}</div><div class="file-meta">${ev.startTime} &middot; ${ev.duration} min${method ? ' &middot; '+method.label : ''}</div></div><button class="btn btn-danger btn-sm" onclick="event.stopPropagation();calDeleteEvent('${ev.id}')">&#128465;</button></div>`; }).join('');
}

function calDayClick(dateKey, time) { cal.selectedDate = dateKey; calOpenModal(null, dateKey, time || '09:00'); }

function calOpenModal(eventId, dateKey, defaultTime) {
  const existing = eventId ? cal.events.find(e => e.id === eventId) : null;
  const date = existing ? existing.date : dateKey;
  const subj = existing ? existing.subject : '';
  const time = existing ? existing.startTime : (defaultTime || '09:00');
  const dur = existing ? existing.duration : 25;
  const method = existing ? existing.method : 'pomodoro';
  const color = existing != null ? existing.color : 0;
  calSelectedColor = color;

  const overlay = document.createElement('div');
  overlay.className = 'cal-modal-overlay'; overlay.id = 'cal-modal';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="cal-modal">
      <div class="cal-modal-header"><h3>${existing ? 'Edit Study Session' : 'New Study Session'}</h3><button class="btn btn-ghost btn-sm" onclick="document.getElementById('cal-modal').remove()">&#10005;</button></div>
      <div class="cal-modal-body">
        <div class="field"><label>Subject</label><input class="input" id="cal-subj" value="${escapeHtml(subj)}" placeholder="e.g. Chemistry, Math, History..."></div>
        <div class="grid-2"><div class="field"><label>Date</label><input type="date" class="input" id="cal-date" value="${date}"></div><div class="field"><label>Start Time</label><input type="time" class="input" id="cal-time" value="${time}"></div></div>
        <div class="field"><label>Duration (minutes)</label><input type="number" class="input" id="cal-dur" value="${dur}" min="5" max="240" step="5"></div>
        <div class="field"><label>Study Method</label><select class="input" id="cal-method">${STUDY_METHODS.map(m => `<option value="${m.key}" ${m.key===method?'selected':''}>${m.label} - ${m.desc}</option>`).join('')}</select></div>
        <div class="field"><label>Color</label><div class="cal-color-swatches">${CAL_COLORS.map((c,i) => `<div class="cal-swatch ${i===color?'active':''}" style="background:${c.dot}" onclick="calPickColor(${i})" data-ci="${i}"></div>`).join('')}</div></div>
      </div>
      <div class="cal-modal-footer">
        ${existing ? `<button class="btn btn-danger" onclick="calDeleteEvent('${eventId}');document.getElementById('cal-modal').remove()">Delete</button>` : ''}
        <div style="flex:1"></div>
        <button class="btn btn-secondary" onclick="document.getElementById('cal-modal').remove()">Cancel</button>
        <button class="btn btn-primary" onclick="calSaveEvent('${eventId || ''}')">Save</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById('cal-subj').focus();
}

let calSelectedColor = 0;
function calPickColor(i) { calSelectedColor = i; document.querySelectorAll('.cal-swatch').forEach(s => s.classList.toggle('active', parseInt(s.dataset.ci) === i)); }
function calEditEvent(id) { const ev = cal.events.find(e => e.id === id); if (!ev) return; calSelectedColor = ev.color || 0; calOpenModal(id); }

function calSaveEvent(eventId) {
  const subj = document.getElementById('cal-subj').value.trim();
  const date = document.getElementById('cal-date').value;
  const time = document.getElementById('cal-time').value;
  const dur = parseInt(document.getElementById('cal-dur').value) || 25;
  const method = document.getElementById('cal-method').value;
  if (!subj) { toast('Enter a subject', 'error'); return; }
  if (!date) { toast('Pick a date', 'error'); return; }
  if (eventId) { const ev = cal.events.find(e => e.id === eventId); if (ev) { ev.subject = subj; ev.date = date; ev.startTime = time; ev.duration = dur; ev.method = method; ev.color = calSelectedColor; } }
  else { cal.events.push({ id: crypto.randomUUID(), subject: subj, date, startTime: time, duration: dur, method, color: calSelectedColor }); }
  saveCalEvents(); document.getElementById('cal-modal')?.remove(); renderCalendar(); toast(eventId ? 'Session updated' : 'Session added', 'success');
}

function calDeleteEvent(id) { cal.events = cal.events.filter(e => e.id !== id); saveCalEvents(); renderCalendar(); toast('Session deleted', 'info'); }

const pomo = { running: false, phase: 'focus', session: 1, totalSessions: 4, focusMin: 25, breakMin: 5, timeLeft: 25 * 60, interval: null };

function pomoToggle() {
  if (pomo.running) { clearInterval(pomo.interval); pomo.running = false; const btn = document.getElementById('pomo-start'); if (btn) btn.innerHTML = '&#9654; Resume'; }
  else { pomo.running = true; const btn = document.getElementById('pomo-start'); if (btn) btn.innerHTML = '&#9208; Pause'; pomo.interval = setInterval(pomoTick, 1000); }
}

function pomoTick() {
  pomo.timeLeft--;
  if (pomo.timeLeft <= 0) {
    if (pomo.phase === 'focus') { toast('Focus time done! Take a break', 'success'); pomo.phase = 'break'; pomo.timeLeft = pomo.breakMin * 60; }
    else { pomo.session++; if (pomo.session > pomo.totalSessions) { toast('All sessions complete! Great work!', 'success'); pomoReset(); return; } toast(`Break over! Starting session ${pomo.session}`, 'info'); pomo.phase = 'focus'; pomo.timeLeft = pomo.focusMin * 60; }
  }
  pomoRenderTime();
}

function pomoReset() { clearInterval(pomo.interval); pomo.running = false; pomo.phase = 'focus'; pomo.session = 1; pomo.timeLeft = pomo.focusMin * 60; pomoRenderTime(); const btn = document.getElementById('pomo-start'); if (btn) btn.innerHTML = '&#9654; Start'; }
function pomoSkip() { if (pomo.phase === 'focus') { pomo.phase = 'break'; pomo.timeLeft = pomo.breakMin * 60; } else { pomo.session++; if (pomo.session > pomo.totalSessions) { pomoReset(); return; } pomo.phase = 'focus'; pomo.timeLeft = pomo.focusMin * 60; } pomoRenderTime(); }
function pomoUpdateSettings() { const f = parseInt(document.getElementById('pomo-focus-min')?.value) || 25; const b = parseInt(document.getElementById('pomo-break-min')?.value) || 5; const s = parseInt(document.getElementById('pomo-sessions')?.value) || 4; pomo.focusMin = f; pomo.breakMin = b; pomo.totalSessions = s; if (!pomo.running) { pomo.timeLeft = pomo.phase === 'focus' ? f * 60 : b * 60; pomoRenderTime(); } }

function pomoRenderTime() {
  const min = Math.floor(pomo.timeLeft / 60); const sec = pomo.timeLeft % 60;
  const el = document.getElementById('pomo-time'); if (el) el.textContent = `${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  const phase = document.getElementById('pomo-phase');
  if (phase) { phase.textContent = pomo.phase === 'focus' ? 'Focus Time' : 'Break Time'; phase.style.color = pomo.phase === 'focus' ? 'var(--blue)' : 'var(--green)'; }
  const info = document.getElementById('pomo-session-info'); if (info) info.textContent = `Session ${pomo.session} of ${pomo.totalSessions}`;
  const ring = document.getElementById('pomo-ring');
  if (ring) { const total = pomo.phase === 'focus' ? pomo.focusMin * 60 : pomo.breakMin * 60; const pct = pomo.timeLeft / total; const circumference = 2 * Math.PI * 82; ring.setAttribute('stroke-dashoffset', String(circumference * (1 - pct))); ring.setAttribute('stroke', pomo.phase === 'focus' ? 'var(--blue)' : 'var(--green)'); }
}

async function renderSettings() {
  const root = document.getElementById('content-root');
  const name = escapeHtml(userProfile?.full_name || getUserDisplayName());
  const avatarUrl = userProfile?.avatar_url || '';
  const schoolBoard = escapeHtml(userProfile?.school_board || '');
  const gradeMajor = escapeHtml(userProfile?.grade_major || '');
  const bio = escapeHtml(userProfile?.bio_message || '');

  root.innerHTML = `
    <div class="settings-page">
      <div class="card">
        <div class="card-header">
          <span style="font-size:20px">&#128100;</span>
          <span class="card-title">Profile Settings</span>
        </div>
        <div class="card-body">
          <div class="settings-avatar-row">
            <div class="settings-avatar-wrap">
              <div class="settings-avatar-preview" id="settings-avatar-preview">
                ${avatarUrl
                  ? `<img src="${avatarUrl}" alt="" />`
                  : `<span class="settings-avatar-initials">${getUserInitial()}</span>`}
              </div>
              <div class="settings-avatar-overlay" onclick="document.getElementById('avatar-file-input').click()">
                &#128247;
              </div>
            </div>
            <div style="flex:1">
              <div class="font-semibold" style="margin-bottom:4px">Profile Photo</div>
              <div class="text-xs text-muted" style="margin-bottom:10px">Click your avatar to upload a new photo (JPG, PNG, WebP)</div>
              <input type="file" id="avatar-file-input" accept="image/jpeg,image/png,image/gif,image/webp" style="display:none" onchange="handleAvatarUpload(this)" />
            </div>
          </div>

          <div class="settings-divider"></div>

          <div class="field">
            <label>Full Name</label>
            <input class="input" id="settings-name" type="text" value="${name}" placeholder="Your full name" />
          </div>
          <div class="field">
            <label>School Board</label>
            <input class="input" id="settings-school-board" type="text" value="${schoolBoard}" placeholder="e.g. IB, AP, GCSE, Ontario Curriculum, CBSE..." />
          </div>
          <div class="field">
            <label>Grade / College Major</label>
            <input class="input" id="settings-grade-major" type="text" value="${gradeMajor}" placeholder="e.g. Grade 11, Computer Science, Pre-Med..." />
          </div>
          <div class="field">
            <label>Your Vibe &#10024; <span class="text-muted" style="font-weight:400;font-size:12px">(a friendly or hilariously unhinged message about yourself)</span></label>
            <textarea class="input" id="settings-bio" rows="3" placeholder="e.g. I run on caffeine and existential dread, but somehow pass my exams &#128517;">${bio}</textarea>
          </div>

          <div class="settings-actions">
            <button class="btn btn-primary" id="btn-save-settings" onclick="saveSettings(this)">&#10003; Save Changes</button>
          </div>
        </div>
      </div>
    </div>`;
}

async function handleAvatarUpload(input) {
  const file = input.files[0];
  if (!file) return;
  const token = await getToken();
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(API + '/me/avatar', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token },
      body: fd,
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'Upload failed');
    userProfile = { ...userProfile, avatar_url: data.avatar_url };
    const preview = document.getElementById('settings-avatar-preview');
    if (preview) preview.innerHTML = `<img src="${data.avatar_url}?t=${Date.now()}" alt="" />`;
    renderSidebar();
    toast('Profile picture updated', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
  input.value = '';
}

async function saveSettings(btn) {
  const name = document.getElementById('settings-name')?.value.trim();
  const schoolBoard = document.getElementById('settings-school-board')?.value.trim();
  const gradeMajor = document.getElementById('settings-grade-major')?.value.trim();
  const bioMessage = document.getElementById('settings-bio')?.value.trim();
  const origLabel = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin">&#10227;</span> Saving...';
  try {
    await api('/me', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ full_name: name, school_board: schoolBoard, grade_major: gradeMajor, bio_message: bioMessage }),
    });
    userProfile = { ...userProfile, full_name: name, school_board: schoolBoard, grade_major: gradeMajor, bio_message: bioMessage };
    renderSidebar();
    toast('Settings saved!', 'success');
  } catch {} finally {
    btn.disabled = false;
    btn.innerHTML = origLabel;
  }
}

initAuth();
