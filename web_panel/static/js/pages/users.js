/* 用户管理：全部用户 / 黑名单 / 通行证 / 拦截篮（分页 + 操作）。 */

import { api, isMiniApp } from '../api.js';
import { el, clear, spinner, toast, badge, chips, confirmDialog, modal, askPauseDuration, table, pager, fmtTime } from '../ui.js';

const PAGE_SIZE = 20;
const SNIPPET_BEFORE = 30;
const SNIPPET_AFTER = 80;

const TIMELINE_LABELS = { user_to_admin: '用户', admin_to_user: '管理员', broadcast_to_user: '广播' };

const state = { tab: 'users', query: '', selected: new Set() };

function listUrl(path, page) {
  const params = new URLSearchParams({ page: String(page), per_page: String(PAGE_SIZE) });
  if (state.query) params.set('q', state.query);
  return `${path}?${params}`;
}

function searchBar(onSearch) {
  const input = el('input', {
    class: 'input',
    type: 'search',
    placeholder: '搜索 ID / 昵称 / 用户名 / 消息内容',
    value: state.query,
  });
  const submit = () => {
    state.query = input.value.trim();
    onSearch();
  };
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(); });
  input.addEventListener('input', () => { if (!input.value && state.query) submit(); });
  return el('div', { class: 'search-bar' }, input, el('button', { class: 'btn sm primary', onClick: submit }, '搜索'));
}

function emptyText(text) {
  return state.query ? `没有找到和「${state.query}」相关的记录。` : text;
}

/* 截取命中关键词前后的一段，高亮关键词 */
function matchSnippet(content) {
  const text = (content || '').replace(/\s+/g, ' ').trim();
  const index = text.toLowerCase().indexOf(state.query.toLowerCase());
  if (index < 0) return text.slice(0, SNIPPET_BEFORE + SNIPPET_AFTER);
  const start = Math.max(0, index - SNIPPET_BEFORE);
  const end = index + state.query.length;
  return el('span', {},
    start > 0 ? '…' : null,
    text.slice(start, index),
    el('mark', {}, text.slice(index, end)),
    text.slice(end, end + SNIPPET_AFTER),
    end + SNIPPET_AFTER < text.length ? '…' : null,
  );
}

function matchCell(row) {
  if (!row.matched_at) return el('span', { class: 'muted small' }, '资料匹配');
  const blocked = row.matched_source === 'filtered';
  const details = [fmtTime(row.matched_at)];
  if (row.match_count > 1) details.push(`共 ${row.match_count} 条`);
  if (row.matched_forward) details.push(`转发自 ${row.matched_forward}`);
  if (blocked && row.matched_reason) details.push(row.matched_reason);
  return el('div', { class: 'match-cell' },
    el('div', {}, row.matched_content ? matchSnippet(row.matched_content) : '（无文字）'),
    el('div', { class: 'muted small' }, blocked ? badge('被拦截', 'danger') : badge('用户发送', 'ok'), ` ${details.join(' · ')}`),
  );
}

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

/* 多选用户后加入分组：可以同时勾选多个分组，也可以顺手新建一个 */
async function groupDialog(userIds, onDone) {
  let groups = [];
  try {
    groups = (await api.get('/api/user-groups')).items;
  } catch (err) {
    toast(err.message, 'error');
    return;
  }
  const boxes = groups.map((group) => ({ name: group.name, box: el('input', { type: 'checkbox' }) }));
  const newGroup = el('input', { class: 'input', placeholder: '新分组名称（可留空）' });
  const dialog = modal({
    title: `把 ${userIds.length} 位用户加入分组`,
    body: el('div', {},
      boxes.length
        ? el('div', { class: 'check-list' }, boxes.map(({ name, box }) => el('label', {}, box, name)))
        : el('div', { class: 'muted small', style: { marginBottom: '10px' } }, '还没有分组，在下面新建一个吧。'),
      el('div', { class: 'field' }, el('label', {}, '新建分组'), newGroup),
    ),
    footer: [
      el('button', { class: 'btn', onClick: () => dialog.close() }, '取消'),
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          const names = boxes.filter(({ box }) => box.checked).map(({ name }) => name);
          const created = newGroup.value.trim();
          if (!names.length && !created) {
            toast('请选择或新建一个分组', 'error');
            return;
          }
          try {
            if (created) {
              await api.post('/api/user-groups', { name: created });
              names.push(created);
            }
            const result = await api.post('/api/user-groups/bulk-add', { user_ids: userIds, groups: names });
            toast(`已加入 ${result.added} 人次${result.skipped ? `，${result.skipped} 人次已在分组里` : ''}`, 'ok');
            dialog.close();
            onDone();
          } catch (err) { toast(err.message, 'error'); }
        },
      }, '加入'),
    ],
  });
}

function highlight(text) {
  const index = state.query ? text.toLowerCase().indexOf(state.query.toLowerCase()) : -1;
  if (index < 0) return text;
  const end = index + state.query.length;
  return [text.slice(0, index), el('mark', {}, text.slice(index, end)), text.slice(end)];
}

function timelineItem(item) {
  const blocked = item.kind === 'filtered';
  const label = blocked ? '被拦截' : (TIMELINE_LABELS[item.direction] || item.direction);
  const meta = [label, fmtTime(item.created_at)];
  if (item.forward_source) meta.push(`转发自 ${item.forward_source}`);
  if (item.reason) meta.push(item.reason);
  return el('div', { class: `timeline-item ${blocked ? 'blocked' : ''}`.trim() },
    el('div', { class: 'muted small' }, meta.join(' · ')),
    el('div', { class: 'timeline-text' }, item.content ? highlight(item.content) : `[${item.media_type || '无文字'}]`),
  );
}

/* 用户详情：备注、标签和只读的消息记录，这里不能回复 */
function userDetail(row, onDone) {
  const noteInput = el('textarea', { class: 'input', rows: '2', placeholder: '只有管理员能看到，可以被搜索' });
  noteInput.value = row.note || '';
  const tagsInput = el('input', { class: 'input', value: (row.tags || []).join(', '), placeholder: '逗号分隔，例如 老客户, 投诉过' });
  const groupsNode = el('div', { class: 'muted small' });
  const timeline = el('div', { class: 'timeline' }, spinner());
  const dialog = modal({
    title: `${row.first_name || '用户'}（${row.user_id}）`,
    wide: true,
    body: el('div', {},
      el('div', { class: 'field' }, el('label', {}, '备注'), noteInput),
      el('div', { class: 'field' }, el('label', {}, '标签'), tagsInput),
      groupsNode,
      el('h3', { style: { margin: '14px 0 8px' } }, state.query ? '命中消息的上下文' : '最近的消息'),
      timeline,
    ),
    footer: [
      el('button', { class: 'btn', onClick: () => dialog.close() }, '关闭'),
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          try {
            await api.put(`/api/users/${row.user_id}/profile`, { note: noteInput.value, tags: tagsInput.value });
            toast('已保存', 'ok');
            dialog.close();
            onDone();
          } catch (err) { toast(err.message, 'error'); }
        },
      }, '保存'),
    ],
  });

  const params = new URLSearchParams();
  if (state.query) params.set('q', state.query);
  api.get(`/api/users/${row.user_id}/timeline?${params}`).then((data) => {
    groupsNode.textContent = data.groups.length ? `所在分组：${data.groups.join('、')}` : '还不在任何分组里';
    clear(timeline);
    if (!data.items.length) timeline.appendChild(el('div', { class: 'muted small' }, '还没有消息记录。'));
    data.items.forEach((item) => timeline.appendChild(timelineItem(item)));
  }).catch((err) => {
    clear(timeline);
    timeline.appendChild(el('div', { class: 'muted small' }, err.message));
  });
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
    let appealPage = 1;
    const resetAndLoad = () => { page = 1; appealPage = 1; load(); };

    state.query = '';
    state.selected = new Set();
    container.appendChild(tabsBar(resetAndLoad));
    container.appendChild(searchBar(resetAndLoad));
    container.appendChild(body);

    async function load() {
      clear(body);
      body.appendChild(spinner());

      try {
        if (state.tab === 'users') await loadUsers();
        else if (state.tab === 'blacklist') await loadBlacklist();
        else if (state.tab === 'exemptions') await loadExemptions();
        else await loadFiltered();
      } catch (err) {
        clear(body);
        body.appendChild(el('div', { class: 'card muted small' }, err.message));
      }
    }

    /* 一键收回：先查实际人数，确认时让管理员看到影响范围 */
    async function revokeAll(target) {
      const labels = {
        verification: { title: '收回全部验证', key: 'verified_users', effect: '这些用户下次发消息都要重新完成验证（管理员不受影响）。' },
        exemption: { title: '取消全部免审查', key: 'exemptions', effect: '这些用户的消息会立即恢复 AI 审查。' },
      }[target];
      try {
        const summary = await api.get('/api/moderation/summary');
        const count = summary[labels.key];
        if (!count) {
          toast('现在没有需要收回的用户', 'info');
          return;
        }
        if (!(await confirmDialog({
          title: labels.title,
          message: `将影响 ${count} 位用户。${labels.effect}不会通知用户，此操作不能撤销。`,
          confirmText: labels.title,
        }))) return;
        const result = await api.post('/api/moderation/revoke-all', { target });
        toast(`已收回 ${result.count} 位用户`, 'ok');
        load();
      } catch (err) { toast(err.message, 'error'); }
    }

    function revokeCard() {
      return el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '一键收回'),
          el('div', { class: 'row' },
            el('button', { class: 'btn sm danger', onClick: () => revokeAll('verification') }, '收回全部验证'),
            el('button', { class: 'btn sm danger', onClick: () => revokeAll('exemption') }, '取消全部免审查'),
          ),
        ),
      );
    }

    async function runAction(action, okText) {
      try {
        const result = await action();
        toast(typeof okText === 'function' ? okText(result) : okText, 'ok');
        load();
      } catch (err) {
        toast(err.message, 'error');
      }
    }

    async function cancelPass(row) {
      if (!(await confirmDialog({
        title: '取消免审查',
        message: `确定取消 ${row.user_id} 的免审查吗？他之后的消息会重新经过 AI 审查，不会通知用户。`,
        confirmText: '取消免审查',
      }))) return;
      runAction(() => api.del(`/api/exemptions/${row.user_id}`), '已取消免审查');
    }

    async function cancelAppeal(row) {
      if (!(await confirmDialog({
        title: '取消申请',
        message: `确定取消 ${row.user_id} 的暂停审查申请吗？不会通知用户。`,
        confirmText: '取消申请',
      }))) return;
      runAction(() => api.del(`/api/review-appeals/${row.user_id}`), '已取消申请');
    }

    async function approveAppeal(row) {
      const choice = await askPauseDuration({
        title: '批准暂停审查',
        hint: `用户申请暂停审查。拦截理由：${row.blocked_reason || '（未记录）'}`,
      });
      if (!choice) return;
      runAction(() => api.post(`/api/review-appeals/${row.user_id}/approve`, choice),
        (result) => `已暂停审查 ${result.duration_text}`);
    }

    function rejectAppeal(row) {
      runAction(() => api.post(`/api/review-appeals/${row.user_id}/reject`), '已拒绝申请，用户会收到通知');
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
      const data = await api.get(listUrl('/api/users', page));
      const statusCell = (row) => [
        row.is_blacklisted ? badge('黑名单', 'danger') : badge('正常', 'ok'),
        row.exempted ? badge('免审查', 'accent') : null,
        row.pending_appeal ? badge('申请中', 'warn') : null,
      ];
      const selectionBar = el('div', { class: 'selection-bar' });
      const renderSelection = () => {
        clear(selectionBar);
        if (!state.selected.size) return;
        selectionBar.appendChild(el('span', {}, `已选 ${state.selected.size} 人`));
        selectionBar.appendChild(el('button', {
          class: 'btn sm primary',
          onClick: () => groupDialog([...state.selected], () => { state.selected.clear(); load(); }),
        }, '加入分组…'));
        selectionBar.appendChild(el('button', {
          class: 'btn sm',
          onClick: () => { state.selected.clear(); load(); },
        }, '清空'));
      };
      const pageBoxes = [];
      const selectBox = (row) => {
        const box = el('input', { type: 'checkbox', 'aria-label': `选择 ${row.user_id}` });
        box.checked = state.selected.has(row.user_id);
        box.addEventListener('change', () => {
          if (box.checked) state.selected.add(row.user_id);
          else state.selected.delete(row.user_id);
          renderSelection();
        });
        pageBoxes.push(box);
        return box;
      };
      const selectPage = () => {
        for (const box of pageBoxes) {
          box.checked = true;
          box.dispatchEvent(new Event('change'));
        }
      };
      clear(body);
      body.appendChild(revokeCard());
      body.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '全部用户'),
          el('div', { class: 'row' },
            el('button', { class: 'btn sm', onClick: selectPage }, '全选本页'),
            el('button', { class: 'btn sm danger', onClick: () => blockDialog(load) }, '手动拉黑'),
          ),
        ),
        selectionBar,
        table({
          columns: [
            { title: '选择', render: selectBox },
            { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.user_id) },
            {
              title: '昵称',
              render: (row) => el('div', {},
                row.first_name || '-',
                row.tags && row.tags.length ? chips(row.tags) : null,
                row.note ? el('div', { class: 'muted small' }, row.note.slice(0, 60)) : null,
              ),
            },
            { title: '用户名', render: (row) => (row.username ? `@${row.username}` : '-') },
            { title: '状态', render: (row) => el('div', { class: 'conv-badges' }, statusCell(row)) },
            ...(state.query ? [{ title: '消息来源', render: matchCell }] : []),
            { title: '垃圾消息数', render: (row) => row.spam_count || 0 },
            {
              title: '',
              className: 'actions',
              render: (row) => [
                el('button', { class: 'btn sm', onClick: () => userDetail(row, load) }, '详情'),
                !isMiniApp && row.has_conversation
                  ? el('a', { class: 'btn sm', href: `#/conversations/${row.user_id}` }, '查看会话')
                  : null,
                row.pending_appeal ? el('button', { class: 'btn sm', onClick: () => cancelAppeal(row) }, '取消申请') : null,
                row.exempted ? el('button', { class: 'btn sm', onClick: () => cancelPass(row) }, '取消免审查') : null,
                row.is_blacklisted
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
                : el('button', { class: 'btn sm danger', onClick: () => blockDialog(load, String(row.user_id)) }, '拉黑'),
              ],
            },
          ],
          rows: data.items,
          empty: emptyText('还没有用户。'),
        }),
        renderPager(data),
      ));
      renderSelection();
    }

    async function loadBlacklist() {
      const data = await api.get(listUrl('/api/blacklist', page));
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
          empty: emptyText('黑名单空空的，很清净。'),
        }),
        renderPager(data),
      ));
    }

    function appealsCard(data) {
      return el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, `待处理的暂停审查申请（${data.total}）`)),
        table({
          columns: [
            { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.user_id) },
            { title: '用户', render: (row) => `${row.first_name || '-'}${row.username ? ` @${row.username}` : ''}` },
            { title: '拦截理由', render: (row) => row.blocked_reason || '-' },
            { title: '被拦消息', render: (row) => el('div', { class: 'match-cell' }, (row.blocked_content || '-').slice(0, 300)) },
            { title: '申请时间', render: (row) => fmtTime(row.created_at) },
            {
              title: '',
              className: 'actions',
              render: (row) => [
                el('button', { class: 'btn sm primary', onClick: () => approveAppeal(row) }, '批准…'),
                el('button', { class: 'btn sm', onClick: () => rejectAppeal(row) }, '拒绝'),
                el('button', { class: 'btn sm', title: '撤掉这条申请，不通知用户', onClick: () => cancelAppeal(row) }, '取消申请'),
              ],
            },
          ],
          rows: data.items,
        }),
        data.total_pages > 1 ? pager({
          page: data.page,
          totalPages: data.total_pages,
          total: data.total,
          onChange: (next) => { appealPage = next; load(); },
        }) : null,
      );
    }

    async function loadExemptions() {
      const [data, appeals] = await Promise.all([
        api.get(listUrl('/api/exemptions', page)),
        api.get(listUrl('/api/review-appeals', appealPage)),
      ]);
      clear(body);
      if (appeals.total > 0) body.appendChild(appealsCard(appeals));
      body.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '通行证（免 AI 审查）'),
          el('button', { class: 'btn sm primary', onClick: () => exemptionDialog(load) }, '发放通行证'),
        ),
        table({
          columns: [
            { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.user_id) },
            { title: '昵称', render: (row) => row.first_name || '-' },
            {
              title: '类型',
              render: (row) => {
                if (!row.is_active) return badge('已过期', 'warn');
                return row.is_permanent ? badge('永久', 'accent') : badge('限期', '');
              },
            },
            { title: '到期', render: (row) => (row.is_permanent ? '-' : fmtTime(row.expires_at)) },
            { title: '备注', render: (row) => row.reason || '-' },
            {
              title: '',
              className: 'actions',
              render: (row) => el('button', { class: 'btn sm danger', onClick: () => cancelPass(row) }, '取消免审查'),
            },
          ],
          rows: data.items,
          empty: emptyText('还没有发放过通行证。'),
        }),
        renderPager(data),
      ));
    }

    async function loadFiltered() {
      const data = await api.get(listUrl('/api/filtered', page));
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
            {
              title: '内容',
              render: (row) => el('div', { class: 'match-cell' },
                (row.content || '').slice(0, 300) || (row.media_type ? `[${row.media_type}]` : '-'),
                row.forward_source ? el('div', { class: 'muted small' }, `转发自 ${row.forward_source}`) : null,
              ),
            },
            {
              title: '',
              className: 'actions',
              render: (row) => [
                el('button', {
                  class: 'btn sm primary',
                  title: '补发到用户话题并移出拦截篮，不通知用户',
                  onClick: async () => {
                    if (!(await confirmDialog({
                      title: '放行消息',
                      message: '把这条消息补发到用户的话题，并移出拦截篮。不会通知用户。',
                      confirmText: '放行',
                      danger: false,
                    }))) return;
                    runAction(() => api.post(`/api/filtered/${row.id}/release`), '已放行');
                  },
                }, '放行'),
                el('button', {
                  class: 'btn sm',
                  onClick: () => runAction(() => api.del(`/api/filtered/${row.id}`), '已删除'),
                }, '删除'),
              ],
            },
          ],
          rows: data.items,
          empty: emptyText('拦截篮是空的。'),
        }),
        renderPager(data),
      ));
    }

    load();
  },
};
