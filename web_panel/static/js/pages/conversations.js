/* 会话回复：用户列表 + 聊天记录 + 实时新消息 + 直接回复 / 封禁。 */

import { api } from '../api.js';
import * as bus from '../bus.js';
import { el, clear, spinner, toast, badge, confirmDialog, fmtTime, fmtRelative, pager } from '../ui.js';

const PAGE_SIZE = 30;
const HISTORY_SIZE = 50;

const state = {
  page: 1,
  query: '',
  selectedUserId: null,
  history: { page: 1, totalPages: 1, loaded: [] },
  cleanupFns: [],
};

function userBadges(user) {
  const nodes = [];
  if (user.is_blacklisted) nodes.push(badge(user.blacklist_permanent ? '永久拉黑' : '黑名单', 'danger'));
  if (!user.is_verified) nodes.push(badge('未验证', 'warn'));
  if (user.exempted) nodes.push(badge('通行证', 'accent'));
  return nodes;
}

function mediaNode(message) {
  if (!message.media_file_id) return null;
  const url = `/api/media/${encodeURIComponent(message.media_file_id)}`;
  const type = message.media_type;
  if (type === 'photo' || type === 'sticker') {
    return el('img', { src: url, loading: 'lazy', alt: type });
  }
  if (type === 'video' || type === 'animation' || type === 'video_note') {
    return el('video', { src: url, controls: true, preload: 'metadata' });
  }
  if (type === 'voice' || type === 'audio') {
    return el('audio', { src: url, controls: true, preload: 'none' });
  }
  return el('a', { href: url, target: '_blank', rel: 'noopener', class: 'small' }, `查看附件（${type || '文件'}）`);
}

function messageNode(message) {
  const out = message.direction === 'admin_to_user' || message.direction === 'broadcast_to_user';
  const bubble = el('div', { class: 'bubble' });
  if (message.content) bubble.appendChild(el('div', {}, message.content));
  const media = mediaNode(message);
  if (media) bubble.appendChild(media);
  if (!message.content && !media) bubble.appendChild(el('div', { class: 'muted small' }, '（空消息）'));
  return el('div', { class: `msg ${out ? 'out' : ''}`.trim() },
    el('div', {},
      bubble,
      el('div', { class: 'meta' }, `${out ? '管理员' : '用户'} · ${fmtTime(message.created_at)}`),
    ),
  );
}

export default {
  title: '会话回复',
  hint: '新消息实时推送，可直接回复用户',
  render(container, params) {
    state.page = 1;
    state.query = '';
    state.selectedUserId = params && params[0] ? Number(params[0]) : null;
    state.history = { page: 1, totalPages: 1, loaded: [] };

    const listNode = el('div', { class: 'conv-items' });
    const listPager = el('div', {});
    const searchInput = el('input', {
      class: 'input',
      placeholder: '搜索用户 ID / 昵称 / 用户名，回车确认',
      value: state.query,
    });
    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        state.query = searchInput.value.trim();
        state.page = 1;
        loadList();
      }
    });

    const chatHead = el('div', { class: 'chat-head' }, el('span', { class: 'muted' }, '请选择左侧的会话'));
    const chatBody = el('div', { class: 'chat-body' });
    const chatInputArea = el('div', { class: 'chat-input' });
    const chatPanel = el('div', { class: 'chat-panel' }, chatHead, chatBody, chatInputArea);

    container.appendChild(el('div', { class: 'chat-layout' },
      el('div', { class: 'conv-list' }, el('div', { class: 'conv-search' }, searchInput), listNode, listPager),
      chatPanel,
    ));

    /* ---------------- 会话列表 ---------------- */

    async function loadList() {
      clear(listNode);
      listNode.appendChild(spinner());
      let data;
      try {
        const query = new URLSearchParams({ page: String(state.page), per_page: String(PAGE_SIZE) });
        if (state.query) query.set('q', state.query);
        data = await api.get(`/api/conversations?${query}`);
      } catch (err) {
        clear(listNode);
        listNode.appendChild(el('div', { class: 'muted small', style: { padding: '10px' } }, err.message));
        return;
      }

      clear(listNode);
      if (data.items.length === 0) {
        listNode.appendChild(el('div', { class: 'muted small', style: { padding: '10px' } }, '没有找到会话'));
      }
      for (const item of data.items) {
        const preview = item.last_content
          ? item.last_content.slice(0, 40)
          : (item.last_media_type ? `[${item.last_media_type}]` : '（还没有消息）');
        const node = el('div', {
          class: `conv-item ${item.user_id === state.selectedUserId ? 'active' : ''}`.trim(),
          dataset: { userId: String(item.user_id) },
          onClick: () => selectUser(item.user_id),
        },
          el('div', { class: 'line1' },
            el('span', { class: 'who' }, item.first_name || item.username || `用户 ${item.user_id}`),
            el('span', { class: 'when' }, fmtRelative(item.last_message_at || item.last_active)),
          ),
          el('div', { class: 'preview' }, preview),
          el('div', {}, userBadges({ ...item, exempted: false, is_verified: !!item.is_verified })),
        );
        listNode.appendChild(node);
      }

      clear(listPager);
      listPager.appendChild(pager({
        page: data.page,
        totalPages: data.total_pages,
        total: data.total,
        onChange: (page) => {
          state.page = page;
          loadList();
        },
      }));
    }

    /* ---------------- 会话详情 ---------------- */

    async function loadDetail({ keepScroll = false } = {}) {
      if (!state.selectedUserId) return;
      if (!keepScroll) chatBody.appendChild(spinner());
      let data;
      try {
        data = await api.get(`/api/conversations/${state.selectedUserId}?page=${state.history.page}&per_page=${HISTORY_SIZE}`);
      } catch (err) {
        clear(chatBody);
        chatBody.appendChild(el('div', { class: 'muted' }, err.message));
        return;
      }

      const user = data.user;
      state.history.totalPages = data.messages.total_pages;
      state.history.page = data.messages.page;
      state.history.loaded = data.messages.items;

      renderChatHead(user);
      renderChatBody();
      renderChatInput(user);
    }

    function renderChatHead(user) {
      clear(chatHead);
      chatHead.appendChild(el('div', {},
        el('div', { class: 'who' }, `${user.first_name || '未知'} ${user.username ? `(@${user.username})` : ''}`),
        el('div', { class: 'small muted' }, `ID ${user.user_id} · 线程 ${user.thread_id || '-'} · 最近活跃 ${fmtTime(user.last_active)}`),
      ));
      chatHead.appendChild(el('div', { style: { display: 'flex', gap: '6px', alignItems: 'center' } },
        userBadges(user),
        user.is_blacklisted
          ? el('button', {
              class: 'btn sm',
              onClick: async () => {
                if (!(await confirmDialog({ title: '解除封禁', message: `确定把用户 ${user.user_id} 移出黑名单吗？`, confirmText: '解封', danger: false }))) return;
                try {
                  const result = await api.post(`/api/conversations/${user.user_id}/unblock`);
                  toast(result.message || '已解封', 'ok');
                  loadDetail({ keepScroll: true });
                  loadList();
                } catch (err) { toast(err.message, 'error'); }
              },
            }, '解封')
          : el('button', {
              class: 'btn sm danger',
              onClick: () => openBlockDialog(user.user_id),
            }, '封禁'),
      ));
    }

    function renderChatBody() {
      const atBottom = chatBody.scrollHeight - chatBody.scrollTop - chatBody.clientHeight < 80;
      clear(chatBody);

      if (state.history.page < state.history.totalPages) {
        chatBody.appendChild(el('div', { style: { textAlign: 'center', marginBottom: '10px' } },
          el('button', {
            class: 'btn sm',
            onClick: async () => {
              state.history.page += 1;
              await loadDetail({ keepScroll: true });
            },
          }, '加载更早的消息'),
        ));
      }
      if (state.history.loaded.length === 0) {
        chatBody.appendChild(el('div', { class: 'muted small', style: { textAlign: 'center' } }, '还没有消息记录'));
      }
      for (const message of state.history.loaded) {
        chatBody.appendChild(messageNode(message));
      }
      if (atBottom) chatBody.scrollTop = chatBody.scrollHeight;
    }

    function renderChatInput(user) {
      clear(chatInputArea);
      const textarea = el('textarea', {
        class: 'input',
        placeholder: '输入回复内容，Enter 发送，Shift+Enter 换行',
        rows: '2',
      });
      const sendButton = el('button', { class: 'btn primary' }, '发送');

      async function send() {
        const text = textarea.value.trim();
        if (!text) return;
        sendButton.disabled = true;
        sendButton.textContent = '发送中……';
        try {
          await api.post(`/api/conversations/${user.user_id}/reply`, { text });
          textarea.value = '';
          state.history.page = 1;
          await loadDetail();
          loadList();
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          sendButton.disabled = false;
          sendButton.textContent = '发送';
          textarea.focus();
        }
      }

      textarea.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          send();
        }
      });
      sendButton.addEventListener('click', send);

      chatInputArea.appendChild(textarea);
      chatInputArea.appendChild(sendButton);
      textarea.focus();
    }

    function openBlockDialog(userId) {
      const reasonInput = el('input', { class: 'input', placeholder: '封锁理由（可留空）' });
      const permanentSwitch = el('input', { type: 'checkbox' });
      const mask = el('div', { class: 'modal-mask' });
      const close = () => mask.remove();
      const box = el('div', { class: 'modal' },
        el('header', {}, '拉黑用户'),
        el('div', { class: 'body' },
          el('div', { class: 'field' }, el('label', {}, '理由'), reasonInput),
          el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, permanentSwitch, '永久封禁（不可自助解封）'),
        ),
        el('footer', {},
          el('button', { class: 'btn', onClick: close }, '取消'),
          el('button', {
            class: 'btn danger',
            onClick: async () => {
              try {
                const result = await api.post(`/api/conversations/${userId}/block`, {
                  reason: reasonInput.value.trim(),
                  permanent: permanentSwitch.checked,
                });
                toast(result.message || '已拉黑', 'ok');
                close();
                loadDetail({ keepScroll: true });
                loadList();
              } catch (err) {
                toast(err.message, 'error');
              }
            },
          }, '确认封禁'),
        ),
      );
      mask.appendChild(box);
      mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
      document.body.appendChild(mask);
      reasonInput.focus();
    }

    async function selectUser(userId) {
      state.selectedUserId = userId;
      state.history.page = 1;
      for (const node of listNode.querySelectorAll('.conv-item')) {
        node.classList.toggle('active', node.dataset.userId === String(userId));
      }
      // 只更新地址栏，不触发 hashchange（避免整页重渲染）
      history.replaceState(null, '', `#/conversations/${userId}`);
      await loadDetail();
    }

    /* ---------------- 实时事件 ---------------- */

    let listTimer = null;
    let detailTimer = null;
    const scheduleListRefresh = () => {
      clearTimeout(listTimer);
      listTimer = setTimeout(loadList, 600);
    };
    const scheduleDetailRefresh = () => {
      clearTimeout(detailTimer);
      detailTimer = setTimeout(() => loadDetail({ keepScroll: true }), 180);
    };

    const offMessage = bus.on('message', (event) => {
      if (event.user_id === state.selectedUserId) {
        scheduleDetailRefresh();
      }
      scheduleListRefresh();
    });
    const offStatus = bus.on('user_status', () => {
      scheduleListRefresh();
      if (state.selectedUserId) scheduleDetailRefresh();
    });

    state.cleanupFns.push(offMessage, offStatus, () => {
      clearTimeout(listTimer);
      clearTimeout(detailTimer);
    });

    /* ---------------- 启动 ---------------- */

    loadList().then(() => {
      if (state.selectedUserId) loadDetail();
    });

    return () => {
      for (const fn of state.cleanupFns.splice(0)) {
        try { fn(); } catch (err) { console.error(err); }
      }
    };
  },
};
