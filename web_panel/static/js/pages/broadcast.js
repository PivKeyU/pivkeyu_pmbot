/* 广播群发：发送、分组管理、投递记录。 */

import { api } from '../api.js';
import * as bus from '../bus.js';
import { el, clear, spinner, toast, badge, confirmDialog, table, pager, fmtTime, selectInput } from '../ui.js';

const PAGE_SIZE = 15;

export default {
  title: '广播群发',
  render(container) {
    let page = 1;
    let groups = [];

    const sendCard = el('div', { class: 'card' });
    const groupsCard = el('div', { class: 'card' });
    const historyCard = el('div', { class: 'card' });
    container.appendChild(sendCard);
    container.appendChild(groupsCard);
    container.appendChild(historyCard);

    /* ---------------- 广播发送 ---------------- */

    function renderSend() {
      clear(sendCard);
      const groupSelect = selectInput({
        options: [{ value: '', label: `全部用户（不含黑名单）` },
          ...groups.map((group) => ({ value: group.name, label: `${group.name}（${group.member_count} 人）` }))],
        value: '',
      });
      const textarea = el('textarea', { class: 'input', rows: '5', placeholder: '要广播的内容……' });
      const sendButton = el('button', { class: 'btn primary' }, '开始广播');

      sendButton.addEventListener('click', async () => {
        const text = textarea.value.trim();
        if (!text) {
          toast('广播内容不能为空', 'error');
          return;
        }
        const scope = groupSelect.value ? `分组「${groupSelect.value}」` : '全部用户';
        if (!(await confirmDialog({
          title: '确认广播',
          message: `将向 ${scope} 发送这条消息，过程中请勿关闭机器人。确定开始吗？`,
          confirmText: '开始发送',
          danger: false,
        }))) return;

        sendButton.disabled = true;
        sendButton.textContent = '投递中……';
        try {
          const result = await api.post('/api/broadcast', { text, group_name: groupSelect.value || undefined });
          toast(`已开始投递，共 ${result.total} 位收件人，下方记录会自动刷新`, 'ok');
          textarea.value = '';
          scheduleHistoryRefresh(3000);
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          sendButton.disabled = false;
          sendButton.textContent = '开始广播';
        }
      });

      sendCard.appendChild(el('div', { class: 'card-head' }, el('h3', {}, '发起广播')));
      sendCard.appendChild(el('div', { class: 'field' }, el('label', {}, '发送范围'), groupSelect));
      sendCard.appendChild(el('div', { class: 'field' }, el('label', {}, '内容'), textarea));
      sendCard.appendChild(el('div', { class: 'row' },
        sendButton,
        el('span', { class: 'muted small' }, '广播会同步镜像到各用户的论坛话题，并记录投递结果。'),
      ));
    }

    /* ---------------- 分组管理 ---------------- */

    async function loadGroups() {
      clear(groupsCard);
      groupsCard.appendChild(spinner());
      let data;
      try {
        data = await api.get('/api/user-groups');
      } catch (err) {
        clear(groupsCard);
        groupsCard.appendChild(el('div', {}, err.message));
        return;
      }
      groups = data.items;
      clear(groupsCard);

      const nameInput = el('input', { class: 'input', placeholder: '新分组名称' });
      const descInput = el('input', { class: 'input', placeholder: '备注（可留空）' });
      const createButton = el('button', {
        class: 'btn sm primary',
        onClick: async () => {
          const name = nameInput.value.trim();
          if (!name) {
            toast('分组名不能为空', 'error');
            return;
          }
          try {
            await api.post('/api/user-groups', { name, description: descInput.value.trim() });
            toast('分组已创建', 'ok');
            nameInput.value = '';
            descInput.value = '';
            await loadGroups();
            renderSend();
          } catch (err) {
            toast(err.message, 'error');
          }
        },
      }, '创建分组');

      groupsCard.appendChild(el('div', { class: 'card-head' }, el('h3', {}, '用户分组',
        el('span', { class: 'sub' }, `共 ${groups.length} 个`))));
      groupsCard.appendChild(el('div', { class: 'row', style: { marginBottom: '12px' } },
        el('div', { class: 'grow' }, nameInput),
        el('div', { class: 'grow' }, descInput),
        createButton,
      ));

      groupsCard.appendChild(table({
        columns: [
          { title: '分组', render: (row) => row.name },
          { title: '备注', render: (row) => row.description || '-' },
          { title: '人数', render: (row) => row.member_count || 0 },
          {
            title: '',
            className: 'actions',
            render: (row) => el('div', { style: { display: 'flex', gap: '6px', justifyContent: 'flex-end' } },
              el('button', { class: 'btn sm', onClick: () => openMembers(row.name) }, '成员'),
              el('button', {
                class: 'btn sm danger',
                onClick: async () => {
                  if (!(await confirmDialog({ title: '删除分组', message: `确定删除分组「${row.name}」吗？（不会封禁成员）`, confirmText: '删除' }))) return;
                  try {
                    await api.del(`/api/user-groups/${encodeURIComponent(row.name)}`);
                    toast('已删除', 'ok');
                    await loadGroups();
                    renderSend();
                  } catch (err) { toast(err.message, 'error'); }
                },
              }, '删除'),
            ),
          },
        ],
        rows: groups,
        empty: '还没有分组。创建分组后可以按组广播。',
      }));
    }

    function openMembers(groupName) {
      const memberList = el('div', {});
      const userIdInput = el('input', { class: 'input', placeholder: '用户 ID' });
      const mask = el('div', { class: 'modal-mask' });
      const close = () => mask.remove();

      async function loadMembers() {
        clear(memberList);
        memberList.appendChild(spinner());
        try {
          const data = await api.get(`/api/user-groups/${encodeURIComponent(groupName)}/members`);
          clear(memberList);
          memberList.appendChild(table({
            columns: [
              { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.user_id) },
              { title: '昵称', render: (row) => row.first_name || '-' },
              { title: '用户名', render: (row) => (row.username ? `@${row.username}` : '-') },
              {
                title: '',
                className: 'actions',
                render: (row) => el('button', {
                  class: 'btn sm danger',
                  onClick: async () => {
                    try {
                      await api.del(`/api/user-groups/${encodeURIComponent(groupName)}/members/${row.user_id}`);
                      toast('已移除', 'ok');
                      loadMembers();
                      loadGroups();
                    } catch (err) { toast(err.message, 'error'); }
                  },
                }, '移除'),
              },
            ],
            rows: data.items,
            empty: '分组里还没有成员。',
          }));
        } catch (err) {
          clear(memberList);
          memberList.appendChild(el('div', {}, err.message));
        }
      }

      mask.appendChild(el('div', { class: 'modal wide' },
        el('header', {}, `分组「${groupName}」成员`),
        el('div', { class: 'body' },
          el('div', { class: 'row', style: { marginBottom: '12px' } },
            el('div', { class: 'grow' }, userIdInput),
            el('button', {
              class: 'btn sm primary',
              onClick: async () => {
                const userId = Number(userIdInput.value.trim());
                if (!userId) {
                  toast('请输入用户 ID', 'error');
                  return;
                }
                try {
                  await api.post(`/api/user-groups/${encodeURIComponent(groupName)}/members`, { user_id: userId });
                  toast('已添加', 'ok');
                  userIdInput.value = '';
                  loadMembers();
                  loadGroups();
                } catch (err) { toast(err.message, 'error'); }
              },
            }, '添加'),
          ),
          memberList,
        ),
        el('footer', {}, el('button', { class: 'btn', onClick: close }, '关闭')),
      ));
      mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
      document.body.appendChild(mask);
      loadMembers();
    }

    /* ---------------- 广播记录 ---------------- */

    async function loadHistory() {
      clear(historyCard);
      historyCard.appendChild(spinner());
      let data;
      try {
        data = await api.get(`/api/broadcasts?page=${page}&per_page=${PAGE_SIZE}`);
      } catch (err) {
        clear(historyCard);
        historyCard.appendChild(el('div', {}, err.message));
        return;
      }
      clear(historyCard);
      historyCard.appendChild(el('div', { class: 'card-head' },
        el('h3', {}, '广播记录'),
        el('button', { class: 'btn sm', onClick: loadHistory }, '刷新'),
      ));
      historyCard.appendChild(table({
        columns: [
          { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.id) },
          { title: '范围', render: (row) => (row.group_name ? `分组：${row.group_name}` : (row.scope === 'all' ? '全部用户' : row.scope)) },
          { title: '内容', render: (row) => el('div', { style: { maxWidth: '360px' } }, (row.content_preview || '').slice(0, 80)) },
          { title: '成功/失败/总数', render: (row) => `${row.success_count}/${row.failed_count}/${row.total_count}` },
          { title: '状态', render: (row) => (row.success_count + row.failed_count >= row.total_count ? badge('完成', 'ok') : badge('进行中', 'warn')) },
          { title: '时间', render: (row) => fmtTime(row.created_at) },
        ],
        rows: data.items,
        empty: '还没有广播记录。',
      }));
      historyCard.appendChild(pager({
        page: data.page,
        totalPages: data.total_pages,
        total: data.total,
        onChange: (next) => { page = next; loadHistory(); },
      }));
    }

    let historyTimer = null;
    function scheduleHistoryRefresh(delay = 4000) {
      clearTimeout(historyTimer);
      historyTimer = setTimeout(async () => {
        page = 1;
        await loadHistory();
      }, delay);
    }

    const offBroadcast = bus.on('broadcast_done', () => {
      toast('广播已完成', 'ok');
      scheduleHistoryRefresh(400);
    });

    /* ---------------- 启动 ---------------- */

    loadGroups().then(renderSend);
    loadHistory();

    return () => {
      clearTimeout(historyTimer);
      offBroadcast();
    };
  },
};
