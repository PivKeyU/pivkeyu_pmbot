/* Telegram Mini App：人机验证页与手机管理页。 */

import { api, setMiniAppToken, setUnauthorizedHandler } from './api.js';
import { el, clear, spinner, toast } from './ui.js';

import overview from './pages/overview.js';
import usersPage from './pages/users.js';
import configPage from './pages/config.js';
import aiPage from './pages/ai.js';
import automation from './pages/automation.js';
import broadcastPage from './pages/broadcast.js';
import feedsPage from './pages/feeds.js';
import toolsPage from './pages/tools.js';

const root = document.getElementById('app');
const telegram = window.Telegram && window.Telegram.WebApp;
const initData = telegram ? telegram.initData : '';

const TURNSTILE_SCRIPT = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

const TABS = [
  { id: 'overview', label: '概览', icon: '📊', page: overview },
  { id: 'users', label: '用户', icon: '👥', page: usersPage },
  { id: 'config', label: '配置', icon: '⚙️', page: configPage },
  { id: 'ai', label: 'AI', icon: '🤖', page: aiPage },
  { id: 'automation', label: '回复', icon: '💡', page: automation },
  { id: 'broadcast', label: '广播', icon: '📣', page: broadcastPage },
  { id: 'feeds', label: '监控', icon: '📡', page: feedsPage },
  { id: 'tools', label: '工具', icon: '🛠️', page: toolsPage },
];

let cleanup = null;

function renderNotice(title, message) {
  clear(root);
  root.className = '';
  root.appendChild(el('div', { class: 'login-wrap' },
    el('div', { class: 'login-card' }, el('h1', {}, title), el('p', { class: 'sub' }, message)),
  ));
}

function loadTurnstile() {
  return new Promise((resolve, reject) => {
    if (window.turnstile) { resolve(window.turnstile); return; }
    const script = el('script', { src: TURNSTILE_SCRIPT, async: true });
    script.addEventListener('load', () => resolve(window.turnstile));
    script.addEventListener('error', () => reject(new Error('验证组件加载失败')));
    document.head.appendChild(script);
  });
}

function renderVerify(session) {
  clear(root);
  root.className = '';

  const status = el('p', { class: 'sub' }, '女仆正在确认客人是真人，稍等一下下。');
  const widget = el('div', { class: 'turnstile-slot' });
  root.appendChild(el('div', { class: 'login-wrap' },
    el('div', { class: 'login-card' }, el('h1', {}, '小验证'), status, widget),
  ));

  const showFallback = (text) => {
    status.textContent = `${text}可以回到聊天，点「打不开？换普通验证题」。`;
  };

  async function submit(widgetApi, widgetId, token) {
    status.textContent = '正在核对结果……';
    try {
      await api.post('/api/verify', { init_data: initData, token });
    } catch (err) {
      widgetApi.reset(widgetId);
      status.textContent = err.message;
      return;
    }
    if (session.is_admin) {
      toast('验证通过啦', 'ok');
      renderAdmin(session);
      return;
    }
    status.textContent = '验证通过啦，女仆为客人开门。';
    setTimeout(() => telegram.close(), 1200);
  }

  loadTurnstile().then((widgetApi) => {
    const widgetId = widgetApi.render(widget, {
      sitekey: session.turnstile_site_key,
      action: session.turnstile_action,
      theme: 'light',
      language: 'zh-cn',
      // 标准尺寸固定 300px，放不下时改用紧凑尺寸
      size: widget.clientWidth < 300 ? 'compact' : 'normal',
      callback: (token) => submit(widgetApi, widgetId, token),
      'error-callback': () => showFallback('验证组件出错了。'),
      'expired-callback': () => { status.textContent = '验证结果过期了，请再点一次。'; },
    });
  }).catch(() => showFallback('验证组件加载失败。'));
}

function renderAdmin(session) {
  clear(root);
  root.className = '';

  const titleNode = el('h2', {}, '');
  const hintNode = el('span', { class: 'hint' });
  const contentNode = el('div', { class: 'content' });
  const tabbar = el('nav', { class: 'miniapp-tabs' }, TABS.map((tab) => el('a', {
    href: `#/${tab.id}`,
    dataset: { page: tab.id },
  }, el('span', { class: 'tab-icon' }, tab.icon), el('span', {}, tab.label))));

  root.appendChild(el('div', { class: 'miniapp-shell' },
    el('div', { class: 'topbar' }, titleNode, hintNode),
    contentNode,
    tabbar,
  ));

  const renderRoute = () => {
    const [pageId, ...params] = location.hash.replace(/^#\/?/, '').split('/');
    const tab = TABS.find((item) => item.id === pageId) || TABS[0];
    if (cleanup) { try { cleanup(); } catch (err) { console.error(err); } cleanup = null; }

    for (const link of tabbar.querySelectorAll('a')) {
      const active = link.dataset.page === tab.id;
      link.classList.toggle('active', active);
      if (active) link.scrollIntoView({ block: 'nearest', inline: 'center' });
    }
    titleNode.textContent = tab.page.title || tab.label;
    hintNode.textContent = '';
    clear(contentNode);
    try {
      cleanup = tab.page.render(contentNode, params) || null;
    } catch (err) {
      console.error(err);
      contentNode.appendChild(el('div', { class: 'card' }, `页面渲染失败：${err.message}`));
    }
    window.scrollTo(0, 0);
  };

  window.addEventListener('hashchange', renderRoute);
  renderRoute();
}

async function boot() {
  if (!initData) {
    renderNotice('请从 Telegram 打开', '这个页面需要在机器人的聊天里点按钮进入。');
    return;
  }

  telegram.ready();
  telegram.expand();
  if (telegram.setHeaderColor) telegram.setHeaderColor('#fbf5ee');

  clear(root);
  root.appendChild(spinner('女仆正在确认身份……'));

  let session;
  try {
    session = await api.post('/api/session', { init_data: initData });
  } catch (err) {
    renderNotice('打不开啦', err.message);
    return;
  }

  if (session.token) setMiniAppToken(session.token);
  setUnauthorizedHandler(() => renderNotice('会话已过期', '请关闭后从 Telegram 重新打开。'));

  if (session.verification_pending) renderVerify(session);
  else if (session.is_admin) renderAdmin(session);
  else renderNotice('门已经打开啦', '现在没有需要完成的验证，回到聊天继续发消息就好。');
}

boot();
