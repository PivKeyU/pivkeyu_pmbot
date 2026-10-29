/* 会话回复：用户列表 + 聊天记录 + 实时新消息 + 直接回复 / 封禁。 */

import { api } from '../api.js';
import * as bus from '../bus.js';
import { el, clear, spinner, toast, badge, confirmDialog, modal, fmtTime, fmtRelative, pager } from '../ui.js';

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
  if (user.exempted) nodes.push(badge('审查已暂停', 'accent'));
  if (user.pending_appeal) nodes.push(badge('申请暂停审查', 'warn'));
  return nodes;
}

/* 暂停审查弹窗：快捷时长 / 自定义时长 / 永久，返回 { minutes } 或 { permanent: true }，取消返回 null */
function askPauseDuration({ title, hint }) {
  return new Promise((resolve) => {
    const amount = el('input', { class: 'input', type: 'number', min: '1', value: '2', style: { maxWidth: '120px' } });
    const unit = el('select', { class: 'input', style: { maxWidth: '120px' } },
      el('option', { value: '1' }, '分钟'),
      el('option', { value: '60', selected: true }, '小时'),
      el('option', { value: '1440' }, '天'),
    );
    const permanent = el('input', { type: 'checkbox' });
    const errorNode = el('div', { class: 'login-error' });
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      dialog.close();
      resolve(value);
    };
    const presets = [[30, '30 分钟'], [120, '2 小时'], [1440, '1 天'], [10080, '7 天']].map(([minutes, label]) =>
      el('button', { class: 'btn sm', onClick: () => finish({ minutes }) }, label));
    const confirmButton = el('button', {
      class: 'btn primary',
      onClick: () => {
        if (permanent.checked) { finish({ permanent: true }); return; }
        const value = Number(amount.value);
        if (!Number.isFinite(value) || value <= 0) {
          errorNode.textContent = '请填写大于 0 的时长';
          return;
        }
        finish({ minutes: Math.round(value * Number(unit.value)) });
      },
    }, '确定');
    const dialog = modal({
      title,
      body: el('div', {},
        hint ? el('div', { class: 'muted small', style: { marginBottom: '12px' } }, hint) : null,
        el('div', { class: 'field' }, el('label', {}, '快捷时长'), el('div', { class: 'row' }, presets)),
        el('div', { class: 'field' },
          el('label', {}, '自定义时长'),
          el('div', { class: 'row', style: { alignItems: 'center' } }, amount, unit),
        ),
        el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, permanent, '永久暂停（直到手动恢复审查）'),
        errorNode,
      ),
      footer: [el('button', { class: 'btn', onClick: () => finish(null) }, '取消'), confirmButton],
    });
    dialog.mask.addEventListener('click', (event) => { if (event.target === dialog.mask) finish(null); });
  });
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
    el('div', { class: 'msg-col' },
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
    const readAllButton = el('button', {
      class: 'btn sm',
      title: '把所有「待回复」标记清掉（在 TG 里已经回复过时使用）',
      onClick: async () => {
        readAllButton.disabled = true;
        try {
          const result = await api.post('/api/conversations/read-all');
          toast(`已把 ${result.count} 个会话标为已读`, 'ok');
          loadList();
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          readAllButton.disabled = false;
        }
      },
    }, '全部已读');
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
      el('div', { class: 'conv-list' },
        el('div', { class: 'conv-search' }, searchInput, readAllButton),
        listNode,
        listPager,
      ),
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
        const isUnread = item.is_unread !== undefined ? !!item.is_unread : item.last_direction === 'user_to_admin';
        const badges = userBadges({
          ...item, exempted: false, is_verified: !!item.is_verified, pending_appeal: !!item.pending_appeal,
        });
        if (isUnread) badges.unshift(badge('待回复', 'accent'));
        const node = el('div', {
          class: `conv-item ${item.user_id === state.selectedUserId ? 'active' : ''} ${isUnread ? 'unread' : ''}`.trim(),
          dataset: { userId: String(item.user_id) },
          onClick: () => selectUser(item.user_id),
        },
          el('div', { class: 'line1' },
            el('span', { class: 'who' }, item.first_name || item.username || `用户 ${item.user_id}`),
            el('span', { class: 'when' }, fmtRelative(item.last_message_at || item.last_active)),
          ),
          el('div', { class: 'preview' }, preview),
          el('div', { class: 'conv-badges' }, badges),
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

    async function loadDetail({ keepScroll = false, prependOlder = false } = {}) {
      if (!state.selectedUserId) return;
      if (!keepScroll && !prependOlder) chatBody.appendChild(spinner());
      const requestPage = prependOlder ? state.history.page : 1;
      let data;
      try {
        data = await api.get(`/api/conversations/${state.selectedUserId}?page=${requestPage}&per_page=${HISTORY_SIZE}`);
      } catch (err) {
        clear(chatBody);
        chatBody.appendChild(el('div', { class: 'muted' }, err.message));
        return;
      }

      const user = data.user;
      state.history.totalPages = data.messages.total_pages;
      if (prependOlder) {
        state.history.page = data.messages.page;
        const existingIds = new Set(state.history.loaded.map((m) => m.id || `${m.direction}_${m.message_id}`));
        const older = (data.messages.items || []).filter(
          (m) => !existingIds.has(m.id || `${m.direction}_${m.message_id}`),
        );
        state.history.loaded = [...older, ...state.history.loaded];
      } else if (keepScroll && state.history.page > 1 && state.history.loaded.length > 0) {
        // 已加载多页历史时收到新消息：把最新一页里新增的消息合并到末尾，不丢弃已展开的更早消息
        const incoming = data.messages.items || [];
        const existingMap = new Map(
          state.history.loaded.map((m) => [m.id || `${m.direction}_${m.message_id}`, m]),
        );
        for (const item of incoming) {
          existingMap.set(item.id || `${item.direction}_${item.message_id}`, item);
        }
        state.history.loaded = Array.from(existingMap.values());
      } else {
        state.history.page = 1;
        state.history.loaded = data.messages.items || [];
      }

      const firstMount = !chatInputArea.hasChildNodes() || chatInputArea.dataset.userId !== String(user.user_id);
      renderChatHead(user);
      renderChatBody({ forceBottom: !keepScroll && !prependOlder, preservePrepend: prependOlder });
      if (firstMount) {
        chatInputArea.dataset.userId = String(user.user_id);
        renderChatInput(user);
      }
    }

    async function runAction(button, task, okText) {
      button.disabled = true;
      try {
        const result = await task();
        if (okText) toast(typeof okText === 'function' ? okText(result) : okText, 'ok');
        loadDetail({ keepScroll: true });
        loadList();
      } catch (err) {
        toast(err.message, 'error');
        button.disabled = false;
      }
    }

    async function pauseReview(user, appeal) {
      const choice = await askPauseDuration({
        title: appeal ? '批准暂停审查' : '暂停 AI 审查',
        hint: appeal
          ? `用户申请暂停审查。拦截理由：${appeal.blocked_reason || '（未记录）'}`
          : '暂停期间该用户的消息不经过 AI 审查，到期自动恢复；会通知用户。',
      });
      if (!choice) return null;
      return api.post(`/api/conversations/${user.user_id}/review-pause`, choice);
    }

    function renderChatHead(user) {
      clear(chatHead);
      const uid = user.user_id;
      const actions = [];

      const readButton = el('button', { class: 'btn sm', title: '清除这个会话的「待回复」标记' }, '标为已读');
      readButton.addEventListener('click', () => runAction(readButton,
        () => api.post(`/api/conversations/${uid}/read`), '已标为已读'));
      actions.push(readButton);

      if (user.exempted) {
        const resumeButton = el('button', { class: 'btn sm' }, '恢复审查');
        resumeButton.addEventListener('click', () => runAction(resumeButton,
          () => api.del(`/api/conversations/${uid}/review-pause`), '已恢复 AI 审查'));
        actions.push(resumeButton);
      } else {
        const pauseButton = el('button', { class: 'btn sm' }, '暂停审查');
        pauseButton.addEventListener('click', () => runAction(pauseButton,
          () => pauseReview(user, null), (r) => r && `已暂停审查 ${r.duration_text}`));
        actions.push(pauseButton);
      }

      if (user.is_verified) {
        const unverifyButton = el('button', { class: 'btn sm', title: '收回验证结果，用户下次发消息要重新验证' }, '撤销验证');
        unverifyButton.addEventListener('click', async () => {
          if (!(await confirmDialog({
            title: '撤销验证',
            message: `收回用户 ${uid} 的验证结果？他下次发消息时需要重新完成人机验证。`,
            confirmText: '撤销验证',
          }))) return;
          runAction(unverifyButton, () => api.post(`/api/conversations/${uid}/unverify`), '已撤销验证');
        });
        actions.push(unverifyButton);
      } else {
        const verifyButton = el('button', { class: 'btn sm' }, '恢复验证');
        verifyButton.addEventListener('click', () => runAction(verifyButton,
          () => api.post(`/api/conversations/${uid}/verify`), '已恢复验证'));
        actions.push(verifyButton);
      }

      if (user.is_blacklisted) {
        const unblockButton = el('button', { class: 'btn sm' }, '解封');
        unblockButton.addEventListener('click', async () => {
          if (!(await confirmDialog({ title: '解除封禁', message: `确定把用户 ${uid} 移出黑名单吗？`, confirmText: '解封', danger: false }))) return;
          runAction(unblockButton, () => api.post(`/api/conversations/${uid}/unblock`), (r) => (r && r.message) || '已解封');
        });
        actions.push(unblockButton);
      } else {
        actions.push(el('button', { class: 'btn sm danger', onClick: () => openBlockDialog(uid) }, '封禁'));
      }

      chatHead.appendChild(el('div', { class: 'chat-head-main' },
        el('div', { class: 'chat-head-info' },
          el('div', { class: 'who' }, `${user.first_name || '未知'} ${user.username ? `(@${user.username})` : ''}`),
          el('div', { class: 'small muted' }, `ID ${uid} · 线程 ${user.thread_id || '-'} · 最近活跃 ${fmtTime(user.last_active)}`),
          el('div', { class: 'conv-badges' }, userBadges(user)),
        ),
        el('div', { class: 'chat-actions' }, actions),
      ));

      const appeal = user.pending_appeal;
      if (appeal) {
        const approveButton = el('button', { class: 'btn sm primary' }, '批准…');
        approveButton.addEventListener('click', () => runAction(approveButton,
          () => pauseReview(user, appeal), (r) => r && `已暂停审查 ${r.duration_text}`));
        const rejectButton = el('button', { class: 'btn sm' }, '拒绝');
        rejectButton.addEventListener('click', () => runAction(rejectButton,
          () => api.post(`/api/conversations/${uid}/appeal/reject`), '已拒绝申请'));
        chatHead.appendChild(el('div', { class: 'appeal-banner' },
          el('div', {},
            el('strong', {}, '🙋 用户申请暂停审查'),
            el('div', { class: 'small' }, `拦截理由：${appeal.blocked_reason || '（未记录）'} · ${fmtTime(appeal.created_at)}`),
          ),
          el('div', { class: 'chat-actions' }, approveButton, rejectButton),
        ));
      }
    }

    function renderChatBody({ forceBottom = false, preservePrepend = false } = {}) {
      const prevScrollHeight = chatBody.scrollHeight;
      const prevScrollTop = chatBody.scrollTop;
      const atBottom = forceBottom || (prevScrollHeight - prevScrollTop - chatBody.clientHeight < 80);
      clear(chatBody);

      if (state.history.page < state.history.totalPages) {
        chatBody.appendChild(el('div', { style: { textAlign: 'center', marginBottom: '10px' } },
          el('button', {
            class: 'btn sm',
            onClick: async (event) => {
              const btn = event.currentTarget;
              btn.disabled = true;
              btn.textContent = '加载中……';
              state.history.page += 1;
              await loadDetail({ keepScroll: true, prependOlder: true });
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
      if (preservePrepend) {
        chatBody.scrollTop = Math.max(0, chatBody.scrollHeight - prevScrollHeight + prevScrollTop);
      } else if (atBottom) {
        chatBody.scrollTop = chatBody.scrollHeight;
      }
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
    const offAppeal = bus.on('appeal', (event) => {
      toast('有用户申请暂停审查，请在会话里处理', 'info');
      scheduleListRefresh();
      if (event.user_id === state.selectedUserId) scheduleDetailRefresh();
    });
    const offStatus = bus.on('user_status', () => {
      scheduleListRefresh();
      if (state.selectedUserId) scheduleDetailRefresh();
    });

    state.cleanupFns.push(offMessage, offStatus, offAppeal, () => {
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
