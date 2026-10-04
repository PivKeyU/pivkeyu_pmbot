/* 仪表盘：统计概览与定时任务运行状态。 */

import { api } from '../api.js';
import { el, clear, spinner, table, fmtTime, fmtRelative, badge, toast } from '../ui.js';

const CATEGORY_LABELS = {
  rss: 'RSS 订阅',
  tg: 'TG 监听',
  web: '网页监控',
  update: '更新',
  monitor: '监控',
};

function statCard(label, value) {
  return el('div', { class: 'stat' },
    el('div', { class: 'num' }, value),
    el('div', { class: 'label' }, label),
  );
}

function statusBadge(row) {
  if (!row.last_run_at) return badge('未运行', '');
  if (row.consecutive_failures > 0) return badge(`失败 x${row.consecutive_failures}`, 'danger');
  return badge('正常', 'ok');
}

export default {
  title: '仪表盘',
  hint: '每 30 秒自动刷新',
  render(container) {
    const body = el('div', {});
    container.appendChild(body);
    container.appendChild(spinner());

    async function refresh() {
      let data;
      try {
        data = await api.get('/api/overview');
      } catch (err) {
        toast(err.message, 'error');
        return;
      }

      const stats = data.stats;
      const cards = [
        ['接待过的用户', stats.total_users],
        ['已验证用户', stats.verified_users],
        ['黑名单', stats.blocked_users],
        ['通行证', stats.exemptions],
        ['拦截篮', stats.filtered_messages],
        ['待回复话题', stats.pending_topics],
        ['今日消息', stats.messages.today],
        ['消息总量', stats.messages.total],
      ];

      const statusRows = (data.runtime_status || []).map((row) => ({
        ...row,
        category_label: CATEGORY_LABELS[row.category] || row.category,
      }));

      clear(container);
      container.appendChild(body);
      clear(body);

      body.appendChild(el('div', { class: 'stat-grid' }, cards.map(([label, value]) => statCard(label, value))));

      body.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '机器人'),
          el('button', { class: 'btn sm', onClick: refresh }, '刷新'),
        ),
        el('div', { class: 'small' },
          el('div', {}, `Bot：@${data.bot.username || '未知'}（ID ${data.bot.id || '-'}）`),
          el('div', {}, `论坛群：${data.bot.forum_group_id || '未配置'}`),
        ),
      ));

      body.appendChild(el('div', { class: 'card' },
        el('h3', {}, '定时任务运行状态'),
        table({
          columns: [
            { title: '任务', render: (row) => row.name },
            { title: '类别', render: (row) => row.category_label },
            { title: '状态', render: (row) => statusBadge(row) },
            { title: '最近运行', render: (row) => el('span', { title: fmtTime(row.last_run_at) }, fmtRelative(row.last_run_at) || '-') },
            { title: '最近成功', render: (row) => fmtTime(row.last_success_at) },
            { title: '耗时', render: (row) => (row.last_duration_ms ? `${row.last_duration_ms} ms` : '-') },
            { title: '推送数', render: (row) => row.last_sent_count || 0 },
            { title: '错误', render: (row) => (row.last_error ? el('span', { class: 'small', title: row.last_error }, row.last_error.slice(0, 60)) : '-') },
          ],
          rows: statusRows,
          empty: '还没有运行记录，定时任务跑过一轮后就会出现在这里。',
        }),
      ));
    }

    refresh();
    const timer = setInterval(refresh, 30000);
    return () => clearInterval(timer);
  },
};
