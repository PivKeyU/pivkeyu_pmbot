/* 面板主框架：登录门禁、侧边栏路由、WebSocket 连接。 */

import { api, setUnauthorizedHandler, connectEvents } from './api.js';
import * as bus from './bus.js';
import { el, clear, toast, textInput } from './ui.js';

import overview from './pages/overview.js';
import conversations from './pages/conversations.js';
import configPage from './pages/config.js';
import aiPage from './pages/ai.js';
import automation from './pages/automation.js';
import usersPage from './pages/users.js';
import broadcastPage from './pages/broadcast.js';
import feedsPage from './pages/feeds.js';
import toolsPage from './pages/tools.js';

const root = document.getElementById('app');

const NAV = [
  { group: '总览', items: [
    { id: 'overview', label: '仪表盘', icon: '📊', page: overview },
    { id: 'conversations', label: '会话回复', icon: '💬', page: conversations },
  ] },
  { group: '配置', items: [
    { id: 'config', label: '配置中心', icon: '⚙️', page: configPage },
    { id: 'ai', label: 'AI 设置', icon: '🤖', page: aiPage },
    { id: 'automation', label: '回复与关键词', icon: '💡', page: automation },
  ] },
  { group: '用户', items: [
    { id: 'users', label: '用户管理', icon: '👥', page: usersPage },
    { id: 'broadcast', label: '广播群发', icon: '📣', page: broadcastPage },
  ] },
  { group: '订阅与监控', items: [
    { id: 'feeds', label: 'RSS / 监控', icon: '📡', page: feedsPage },
  ] },
  { group: '系统', items: [
    { id: 'tools', label: '网络与更新', icon: '🛠️', page: toolsPage },
  ] },
];

const state = { me: null, cleanup: null, ws: null, wsOnline: false };

function currentRoute() {
  const hash = location.hash.replace(/^#\/?/, '');
  const [pageId, ...rest] = hash.split('/');
  return { pageId: pageId || 'overview', params: rest };
}

function findPage(pageId) {
  for (const group of NAV) {
    for (const item of group.items) {
      if (item.id === pageId) return item;
    }
  }
  return null;
}

function renderLogin(errorText = '') {
  if (state.ws) { state.ws.close(); state.ws = null; }
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  clear(root);

  const passwordInput = textInput({
    type: 'password',
    placeholder: '请输入面板密码',
    onEnter: () => submit(),
  });
  const errorNode = el('div', { class: 'login-error' }, errorText);
  const submitButton = el('button', { class: 'btn primary', style: { width: '100%' }, onClick: () => submit() }, '进入房间');

  async function submit() {
    const password = passwordInput.value;
    if (!password) return;
    errorNode.textContent = '';
    submitButton.disabled = true;
    submitButton.textContent = '正在开门……';
    try {
      await api.post('/api/login', { password });
      toast('欢迎回来，主人 ♪', 'ok');
      await boot();
    } catch (err) {
      errorNode.textContent = err.message || '登录失败';
      passwordInput.value = '';
      passwordInput.focus();
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = '进入房间';
    }
  }

  root.className = '';
  root.appendChild(el('div', { class: 'login-wrap' },
    el('div', { class: 'login-card' },
      el('h1', {}, '片刻月小女仆'),
      el('p', { class: 'sub' }, '欢迎来到女仆的房间 · 请主人验证身份'),
      el('div', { class: 'field' }, passwordInput),
      errorNode,
      submitButton,
    ),
  ));
  passwordInput.focus();
}

function renderShell() {
  clear(root);
  root.className = '';

  const navNode = el('nav', { class: 'nav' });
  const wsDot = el('span', { class: 'small muted' }, '连接中…');
  const contentNode = el('div', { class: 'content' });
  const titleNode = el('h2', {}, '仪表盘');
  const hintNode = el('span', { class: 'hint' });

  for (const group of NAV) {
    navNode.appendChild(el('div', { class: 'group' }, group.group));
    for (const item of group.items) {
      navNode.appendChild(el('a', {
        href: `#/${item.id}`,
        dataset: { page: item.id },
        onClick: () => {},
      }, el('span', {}, item.icon), el('span', {}, item.label)));
    }
  }

  const shell = el('div', { class: 'layout' },
    el('aside', { class: 'sidebar' },
      el('div', { class: 'brand' },
        el('div', { class: 'name' }, '片刻月小女仆'),
        el('div', { class: 'bot' }, state.me && state.me.bot_username ? `@${state.me.bot_username}` : '女仆的房间'),
      ),
      el('div', { class: 'greet' }, '欢迎回来，主人 ♪'),
      navNode,
      el('div', { class: 'sidebar-foot' },
        el('button', {
          onClick: async () => {
            try { await api.post('/api/logout'); } catch (err) { /* 忽略 */ }
            state.me = { authenticated: false };
            renderLogin();
          },
        }, '女仆先退下了'),
      ),
    ),
    el('div', { class: 'main' },
      el('div', { class: 'topbar' }, titleNode, el('div', {}, wsDot, ' ', hintNode)),
      contentNode,
    ),
  );

  clear(root);
  root.appendChild(shell);

  if (!state.ws) {
    state.ws = connectEvents({
      onEvent: (event) => bus.emit(event),
      onStatus: (online) => {
        state.wsOnline = online;
        clear(wsDot);
        wsDot.appendChild(el('span', { class: `badge ${online ? 'ok' : 'warn'}` }, online ? '实时已连接' : '实时已断开'));
      },
    });
  }

  const renderRoute = () => {
    const { pageId, params } = currentRoute();
    const item = findPage(pageId) || NAV[0].items[0];
    if (state.cleanup) { try { state.cleanup(); } catch (err) { console.error(err); } state.cleanup = null; }

    for (const link of navNode.querySelectorAll('a')) {
      link.classList.toggle('active', link.dataset.page === item.id);
    }
    titleNode.textContent = item.page.title || item.label;
    clear(hintNode);
    if (item.page.hint) hintNode.appendChild(document.createTextNode(item.page.hint));

    clear(contentNode);
    try {
      state.cleanup = item.page.render(contentNode, params) || null;
    } catch (err) {
      console.error(err);
      contentNode.appendChild(el('div', { class: 'card' }, `页面渲染失败：${err.message}`));
    }
  };

  if (!state.hashBound) {
    // 只绑定一次：路由分发函数存到 state 上，重登录后不会引用旧 DOM
    window.addEventListener('hashchange', () => {
      if (state.me && state.me.authenticated && state.renderRoute) state.renderRoute();
    });
    state.hashBound = true;
  }
  state.renderRoute = renderRoute;
  renderRoute();
}

function renderSetup(me) {
  if (state.ws) { state.ws.close(); state.ws = null; }
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  clear(root);
  root.className = '';

  const remainingNode = el('p', { class: 'sub' });
  let remaining = Number(me.setup_remaining) || 0;
  const refreshCountdown = () => {
    if (remaining > 0) {
      remainingNode.textContent = `初始化窗口剩余 ${Math.floor(remaining / 60)} 分 ${remaining % 60} 秒，超时后面板将锁定`;
    } else {
      remainingNode.textContent = '初始化窗口已关闭，请刷新页面或重启进程';
    }
  };
  const countdown = setInterval(() => { remaining -= 1; refreshCountdown(); }, 1000);
  refreshCountdown();

  const passwordInput = textInput({ type: 'password', placeholder: '设置门锁密码（至少 8 位）', onEnter: () => submit() });
  const confirmInput = textInput({ type: 'password', placeholder: '再输入一次确认', onEnter: () => submit() });
  const errorNode = el('div', { class: 'login-error' });
  const submitButton = el('button', { class: 'btn primary', style: { width: '100%' }, onClick: () => submit() }, '设好门锁，进入房间');

  async function submit() {
    const password = passwordInput.value;
    if (password.length < 8) {
      errorNode.textContent = '密码至少 8 位';
      return;
    }
    if (password !== confirmInput.value) {
      errorNode.textContent = '两次输入的密码不一致';
      return;
    }
    errorNode.textContent = '';
    submitButton.disabled = true;
    submitButton.textContent = '上锁中……';
    try {
      await api.post('/api/setup', { password });
      clearInterval(countdown);
      toast('门锁已设好，欢迎回来，主人 ♪', 'ok');
      await boot();
    } catch (err) {
      errorNode.textContent = err.message || '设置失败';
      passwordInput.value = '';
      confirmInput.value = '';
      passwordInput.focus();
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = '设好门锁，进入房间';
    }
  }

  root.appendChild(el('div', { class: 'login-wrap' },
    el('div', { class: 'login-card' },
      el('h1', {}, '片刻月小女仆'),
      el('p', { class: 'sub' }, '初次见面！先给房间设一把门锁吧'),
      remainingNode,
      el('div', { class: 'field' }, passwordInput),
      el('div', { class: 'field' }, confirmInput),
      errorNode,
      submitButton,
      el('p', { class: 'muted small', style: { marginTop: '12px' } },
        '密码只保存 SHA256 摘要，不会以明文落盘；登录后可在「配置中心 → 修改面板密码」随时更改。'),
    ),
  ));
  passwordInput.focus();
}

function renderLocked() {
  if (state.ws) { state.ws.close(); state.ws = null; }
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  clear(root);
  root.className = '';

  root.appendChild(el('div', { class: 'login-wrap' },
    el('div', { class: 'login-card' },
      el('h1', {}, '房间已上锁'),
      el('p', { class: 'sub' }, '初始化窗口关闭时一直没有人来设门锁，房间只好先上锁啦。'),
      el('div', { class: 'small', style: { lineHeight: '2' } },
        el('div', {}, '女仆想了想，解锁方式有两条：'),
        el('div', {}, '1. 重启机器人 / 容器，在新的初始化窗口里设置门锁；'),
        el('div', {}, '2. 或在 .env 里写好 WEB_PANEL_PASSWORD=你的密码 再重启。'),
      ),
      el('div', { style: { marginTop: '16px' } },
        el('button', { class: 'btn primary', onClick: () => boot() }, '我弄好了，再检查一次'),
      ),
    ),
  ));
}

async function boot() {
  setUnauthorizedHandler(() => {
    if (state.me && state.me.authenticated) {
      state.me = { authenticated: false };
      toast('会话已过期，请重新登录', 'error');
      renderLogin();
    }
  });

  let me = null;
  try {
    me = await api.get('/api/me');
  } catch (err) {
    clear(root);
    root.className = '';
    root.appendChild(el('div', { class: 'login-wrap' },
      el('div', { class: 'login-card' },
        el('h1', {}, '女仆暂时不在家'),
        el('p', { class: 'sub' }, err.message || '请确认机器人进程正在运行'),
      ),
    ));
    return;
  }

  state.me = me;
  if (me.setup_mode) return renderSetup(me);
  if (me.locked) return renderLocked();
  if (!me.authenticated) renderLogin();
  else renderShell();
}

boot();
