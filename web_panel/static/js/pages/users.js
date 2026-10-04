/* 用户管理：全部用户 / 黑名单 / 通行证 / 拦截篮（分页 + 操作）。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, confirmDialog, table, pager, fmtTime } from '../ui.js';

const PAGE_SIZE = 20;

const state = { tab: 'users' };

function tabsBar(onChange) {
  const tabs = [
    ['users', '全部用户'],
    ['blacklist', '黑名单'],
    ['exemptions', '通行证'],
    ['filtered', '拦截篮'],
  ];
  const bar = el('div', { class: 'tabs' });
  for (const [id, label] of tabs) {
    bar.appendChild(el('button', {
      class: state.tab === id ? 'active' : '',
      dataset: { tab: id },
      onClick: () => {
        state.tab = id;
        for (const button of bar.querySelectorAll('button')) {
          button.classList.toggle('active', button.dataset.tab === id);
        }
        onChange();
      },
    }, label));
  }
  return bar;
}

function blockDialog(onDone, presetUserId = '') {
  const userIdInput = el('input', { class: 'input', placeholder: '用户 ID', value: presetUserId });
  const reasonInput = el('input', { class: 'input', placeholder: '封锁理由（可留空）' });
  const permanent = el('input', { type: 'checkbox' });
  const mask = el('div', { class: 'modal-mask' });
  const close = () => mask.remove();
  mask.appendChild(el('div', { class: 'modal' },
    el('header', {}, '拉黑用户'),
    el('div', { class: 'body' },
      el('div', { class: 'field' }, el('label', {}, '用户 ID'), userIdInput),
      el('div', { class: 'field' }, el('label', {}, '理由'), reasonInput),
      el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, permanent, '永久封禁'),
    ),
    el('footer', {},
      el('button', { class: 'btn', onClick: close }, '取消'),
      el('button', {
        class: 'btn danger',
        onClick: async () => {
          try {
            const result = await api.post('/api/blacklist', {
              user_id: Number(userIdInput.value.trim()),
              reason: reasonInput.value.trim(),
              permanent: permanent.checked,
            });
            toast(result.message || '已拉黑', 'ok');
            close();
            onDone();
          } catch (err) {
            toast(err.message, 'error');
          }
        },
      }, '确认封禁'),
    ),
  ));
  mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
  document.body.appendChild(mask);
  userIdInput.focus();
}

function exemptionDialog(onDone) {
  const userIdInput = el('input', { class: 'input', placeholder: '用户 ID' });
  const daysInput = el('input', { class: 'input', type: 'number', value: '7', min: '1' });
  const reasonInput = el('input', { class: 'input', placeholder: '备注（可留空）' });
  const permanent = el('input', { type: 'checkbox' });
  const mask = el('div', { class: 'modal-mask' });
  const close = () => mask.remove();
  mask.appendChild(el('div', { class: 'modal' },
    el('header', {}, '发放通行证'),
    el('div', { class: 'body' },
      el('div', { class: 'field' }, el('label', {}, '用户 ID'), userIdInput),
      el('div', { class: 'field' }, el('label', {}, '有效天数（永久时忽略）'), daysInput),
      el('div', { class: 'field' }, el('label', {}, '备注'), reasonInput),
      el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, permanent, '永久通行证'),
    ),
    el('footer', {},
      el('button', { class: 'btn', onClick: close }, '取消'),
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          try {
            await api.post('/api/exemptions', {
              user_id: Number(userIdInput.value.trim()),
              days: Number(daysInput.value || 7),
              reason: reasonInput.value.trim(),
              permanent: permanent.checked,
            });
            toast('通行证已发放', 'ok');
            close();
            onDone();
          } catch (err) {
            toast(err.message, 'error');
          }
        },
      }, '发放'),
    ),
  ));
  mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
  document.body.appendChild(mask);
  userIdInput.focus();
}

export default {
  title: '用户管理',
  render(container) {
    const body = el('div', {});
    let page = 1;

    container.appendChild(tabsBar(() => { page = 1; load(); }));
    container.appendChild(body);

    async function load() {
      clear(body);
      body.appendChild(spinner());

      if (state.tab === 'users') return loadUsers();
      if (state.tab === 'blacklist') return loadBlacklist();
      if (state.tab === 'exemptions') return loadExemptions();
      return loadFiltered();
    }

    function renderPager(data) {
      return pager({
        page: data.page,
        totalPages: data.total_pages,
        total: data.total,
        onChange: (next) => { page = next; load(); },
      });
    }

    async function loadUsers() {
      const data = await api.get(`/api/users?page=${page}&per_page=${PAGE_SIZE}`);
      clear(body);
      body.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '全部用户'),
          el('button', { class: 'btn sm danger', onClick: () => blockDialog(load) }, '手动拉黑'),
        ),
        table({
          columns: [
            { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.user_id) },
            { title: '昵称', render: (row) => row.first_name || '-' },
            { title: '用户名', render: (row) => (row.username ? `@${row.username}` : '-') },
            { title: '状态', render: (row) => (row.is_blacklisted ? badge('黑名单', 'danger') : badge('正常', 'ok')) },
            { title: '垃圾消息数', render: (row) => row.spam_count || 0 },
            {
              title: '',
              className: 'actions',
              render: (row) => (row.is_blacklisted
                ? el('button', {
                    class: 'btn sm',
                    onClick: async () => {
                      if (!(await confirmDialog({ title: '解除封禁', message: `确定解封 ${row.user_id} 吗？`, confirmText: '解封', danger: false }))) return;
                      try {
                        await api.del(`/api/blacklist/${row.user_id}`);
                        toast('已解封', 'ok');
                        load();
                      } catch (err) { toast(err.message, 'error'); }
                    },
                  }, '解封')
                : el('button', { class: 'btn sm danger', onClick: () => blockDialog(load, String(row.user_id)) }, '拉黑')),
            },
          ],
          rows: data.items,
        }),
        renderPager(data),
      ));
    }

    async function loadBlacklist() {
      const data = await api.get(`/api/blacklist?page=${page}&per_page=${PAGE_SIZE}`);
      clear(body);
      body.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '黑名单'),
          el('button', { class: 'btn sm danger', onClick: () => blockDialog(load) }, '手动拉黑'),
        ),
        table({
          columns: [
            { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.user_id) },
            { title: '昵称', render: (row) => row.first_name || '-' },
            { title: '用户名', render: (row) => (row.username ? `@${row.username}` : '-') },
            { title: '理由', render: (row) => row.reason || '-' },
            { title: '封禁时间', render: (row) => fmtTime(row.blocked_at) },
            {
              title: '',
              className: 'actions',
              render: (row) => el('button', {
                class: 'btn sm',
                onClick: async () => {
                  if (!(await confirmDialog({ title: '解除封禁', message: `确定解封 ${row.user_id} 吗？`, confirmText: '解封', danger: false }))) return;
                  try {
                    await api.del(`/api/blacklist/${row.user_id}`);
                    toast('已解封', 'ok');
                    load();
                  } catch (err) { toast(err.message, 'error'); }
                },
              }, '解封'),
            },
          ],
          rows: data.items,
          empty: '黑名单空空的，很清净。',
        }),
        renderPager(data),
      ));
    }

    async function loadExemptions() {
      const data = await api.get(`/api/exemptions?page=${page}&per_page=${PAGE_SIZE}`);
      clear(body);
      body.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '通行证（免 AI 审查）'),
          el('button', { class: 'btn sm primary', onClick: () => exemptionDialog(load) }, '发放通行证'),
        ),
        table({
          columns: [
            { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.user_id) },
            { title: '昵称', render: (row) => row.first_name || '-' },
            { title: '类型', render: (row) => (row.is_permanent ? badge('永久', 'accent') : badge('限期', '')) },
            { title: '到期', render: (row) => (row.is_permanent ? '-' : fmtTime(row.expires_at)) },
            { title: '备注', render: (row) => row.reason || '-' },
            {
              title: '',
              className: 'actions',
              render: (row) => el('button', {
                class: 'btn sm danger',
                onClick: async () => {
                  if (!(await confirmDialog({ title: '移除通行证', message: `确定移除 ${row.user_id} 的通行证吗？`, confirmText: '移除' }))) return;
                  try {
                    await api.del(`/api/exemptions/${row.user_id}`);
                    toast('已移除', 'ok');
                    load();
                  } catch (err) { toast(err.message, 'error'); }
                },
              }, '移除'),
            },
          ],
          rows: data.items,
          empty: '还没有发放过通行证。',
        }),
        renderPager(data),
      ));
    }

    async function loadFiltered() {
      const data = await api.get(`/api/filtered?page=${page}&per_page=${PAGE_SIZE}`);
      clear(body);
      body.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '拦截篮'),
          el('button', {
            class: 'btn sm danger',
            onClick: async () => {
              if (!(await confirmDialog({ title: '清空拦截篮', message: '确定清空全部拦截记录吗？此操作不可撤销。', confirmText: '清空' }))) return;
              try {
                const result = await api.del('/api/filtered');
                toast(`已清空 ${result.deleted} 条`, 'ok');
                page = 1;
                load();
              } catch (err) { toast(err.message, 'error'); }
            },
          }, '清空'),
        ),
        table({
          columns: [
            { title: '时间', render: (row) => fmtTime(row.filtered_at) },
            { title: '用户', render: (row) => `${row.first_name || ''} ${row.username ? `@${row.username}` : ''} (${row.user_id})`.trim() },
            { title: '理由', render: (row) => row.reason || '-' },
            { title: '内容', render: (row) => el('div', { style: { maxWidth: '420px', whiteSpace: 'pre-wrap' } }, (row.content || '').slice(0, 300) || (row.media_type ? `[${row.media_type}]` : '-')) },
            {
              title: '',
              className: 'actions',
              render: (row) => el('button', {
                class: 'btn sm',
                onClick: async () => {
                  try {
                    await api.del(`/api/filtered/${row.id}`);
                    toast('已删除', 'ok');
                    load();
                  } catch (err) { toast(err.message, 'error'); }
                },
              }, '删除'),
            },
          ],
          rows: data.items,
          empty: '拦截篮是空的。',
        }),
        renderPager(data),
      ));
    }

    load();
  },
};
