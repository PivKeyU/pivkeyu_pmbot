/* 订阅与监控：RSS 订阅 / TG 群监听 / 网页监控 / 补货监控 / 监控结果。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, confirmDialog, table, pager, switchBox, chips, selectInput, fmtTime } from '../ui.js';

const EVENT_PAGE_SIZE = 20;
const EVENT_SOURCES = {
  stock: ['补货', 'ok'],
  web: ['网页', ''],
  tg: ['TG', 'accent'],
  rss: ['RSS', ''],
};
const STOCK_STATES = { in: ['有货', 'ok'], out: ['缺货', 'danger'] };

const state = { tab: 'rss' };

/* 机器人推送设置：网页、补货和 TG 监听的表单共用 */
function pushFields(monitor) {
  const enabled = el('input', { type: 'checkbox' });
  enabled.checked = !monitor || monitor.notify_telegram !== false;
  const targets = el('input', {
    class: 'input',
    value: (monitor && monitor.notify_chat_ids) || '',
    placeholder: '留空推送给默认目标；多个 chat_id 用逗号分隔',
  });
  return {
    nodes: [
      el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center', marginBottom: '8px' } }, enabled, '命中后由机器人推送消息'),
      el('div', { class: 'field' }, el('label', {}, '推送目标'), targets),
    ],
    values: () => ({ notify_telegram: enabled.checked, notify_chat_ids: targets.value.trim() }),
  };
}

function runButton(row, reload) {
  const button = el('button', { class: 'btn sm' }, '立即检查');
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const result = await api.post(`/api/web-monitors/${row.id}/run`);
      if (result.error) toast(result.error, 'error');
      else toast(result.sent ? `检查完成，推送了 ${result.sent} 条` : '检查完成，没有需要推送的变化', 'ok');
      reload();
    } catch (err) {
      toast(err.message, 'error');
      button.disabled = false;
    }
  });
  return button;
}

function tabsBar(onChange) {
  const tabs = [
    ['rss', 'RSS 订阅'],
    ['tg', 'TG 群监听'],
    ['web', '网页监控'],
    ['stock', '补货监控'],
    ['events', '监控结果'],
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

function promptDialog({ title, label, placeholder = '', onConfirm }) {
  const input = el('input', { class: 'input', placeholder });
  const mask = el('div', { class: 'modal-mask' });
  const close = () => mask.remove();
  const confirm = async () => {
    const value = input.value.trim();
    if (!value) {
      toast('不能为空', 'error');
      return;
    }
    try {
      await onConfirm(value);
      close();
    } catch (err) {
      toast(err.message, 'error');
    }
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') confirm();
  });
  mask.appendChild(el('div', { class: 'modal' },
    el('header', {}, title),
    el('div', { class: 'body' }, el('div', { class: 'field' }, el('label', {}, label), input)),
    el('footer', {},
      el('button', { class: 'btn', onClick: close }, '取消'),
      el('button', { class: 'btn primary', onClick: confirm }, '确定'),
    ),
  ));
  mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
  document.body.appendChild(mask);
  input.focus();
}

/* ------------------------------------------------------------------ RSS */

async function renderRss(container) {
  const reload = () => {
    clear(container);
    renderRss(container);
  };
  container.appendChild(spinner());
  let data;
  try {
    data = await api.get('/api/rss');
  } catch (err) {
    clear(container);
    container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
    return;
  }
  clear(container);

  // 开关与间隔
  const intervalInput = el('input', { class: 'input', type: 'number', value: String(data.check_interval), style: { maxWidth: '140px' } });
  const settingsCard = el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h3', {}, 'RSS 推送'),
      el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center' } },
        el('span', { class: 'small muted' }, '启用'),
        switchBox({
          checked: data.enabled,
          onChange: async (checked) => {
            try {
              await api.put('/api/rss/settings', { enabled: checked });
              toast(checked ? 'RSS 已启用' : 'RSS 已暂停', 'ok');
            } catch (err) { toast(err.message, 'error'); }
          },
        }),
      ),
    ),
    el('div', { class: 'small muted', style: { marginBottom: '8px' } }, '订阅以 chat_id 为归属（授权用户/群），每隔一段时间抓取一次新条目并推送。'),
    el('div', { class: 'row' },
      el('div', {}, el('label', { class: 'small muted' }, '检查间隔（秒）'), intervalInput),
      el('button', {
        class: 'btn sm',
        onClick: async () => {
          try {
            await api.put('/api/rss/settings', { check_interval: Number(intervalInput.value) });
            toast('间隔已更新', 'ok');
          } catch (err) { toast(err.message, 'error'); }
        },
      }, '保存间隔'),
    ),
  );
  container.appendChild(settingsCard);

  // 授权用户
  const authCard = el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h3', {}, '授权用户', el('span', { class: 'sub' }, '可使用 RSS 命令的用户 ID')),
      el('button', {
        class: 'btn sm',
        onClick: () => promptDialog({
          title: '添加授权用户',
          label: '用户 ID',
          placeholder: '数字 ID',
          onConfirm: async (value) => {
            await api.post('/api/rss/authorized', { user_id: Number(value) });
            toast('已添加', 'ok');
            reload();
          },
        }),
      }, '添加'),
    ),
    data.authorized_users.length
      ? chips(data.authorized_users, async (userId) => {
          try {
            await api.del(`/api/rss/authorized/${userId}`);
            toast('已移除', 'ok');
            reload();
          } catch (err) { toast(err.message, 'error'); }
        })
      : el('div', { class: 'muted small' }, '暂无授权用户，仅管理员可用 RSS 命令。'),
  );
  container.appendChild(authCard);

  // 订阅列表
  const feedsCard = el('div', { class: 'card' });
  feedsCard.appendChild(el('div', { class: 'card-head' },
    el('h3', {}, '订阅列表', el('span', { class: 'sub' }, `共 ${data.feeds.length} 条`)),
    el('button', {
      class: 'btn sm primary',
      onClick: () => addFeedDialog(),
    }, '添加订阅'),
  ));

  feedsCard.appendChild(table({
    columns: [
      { title: '归属 chat', render: (row) => el('span', { class: 'mono' }, row.chat_id) },
      { title: '标题', render: (row) => row.title || '-' },
      { title: '链接', render: (row) => el('a', { href: row.url, target: '_blank', rel: 'noopener', class: 'small' }, row.url.slice(0, 60)) },
      {
        title: '关键词',
        render: (row) => el('div', {},
          row.keywords.length ? chips(row.keywords, async (keyword) => {
            try {
              await api.del('/api/rss/keywords', { chat_id: row.chat_id, url: row.url, keyword });
              toast('已移除', 'ok');
              reload();
            } catch (err) { toast(err.message, 'error'); }
          }) : el('span', { class: 'muted small' }, '（全部条目）'),
          el('button', {
            class: 'btn sm ghost',
            onClick: () => promptDialog({
              title: `为「${row.title}」添加关键词`,
              label: '关键词（命中才推送）',
              onConfirm: async (keyword) => {
                await api.post('/api/rss/keywords', { chat_id: row.chat_id, url: row.url, keyword });
                toast('已添加', 'ok');
                reload();
              },
            }),
          }, '+ 关键词'),
        ),
      },
      {
        title: '',
        className: 'actions',
        render: (row) => el('button', {
          class: 'btn sm danger',
          onClick: async () => {
            if (!(await confirmDialog({ title: '删除订阅', message: `确定删除「${row.title}」吗？`, confirmText: '删除' }))) return;
            try {
              await api.del('/api/rss/feeds', { chat_id: row.chat_id, url: row.url });
              toast('已删除', 'ok');
              reload();
            } catch (err) { toast(err.message, 'error'); }
          },
        }, '删除'),
      },
    ],
    rows: data.feeds,
    empty: '还没有订阅。',
  }));
  container.appendChild(feedsCard);

  function addFeedDialog() {
    const chatInput = el('input', { class: 'input', placeholder: '归属 chat_id（用户 ID 或群 ID）' });
    const urlInput = el('input', { class: 'input', placeholder: 'https://example.com/feed.xml' });
    const mask = el('div', { class: 'modal-mask' });
    const close = () => mask.remove();
    mask.appendChild(el('div', { class: 'modal' },
      el('header', {}, '添加 RSS 订阅'),
      el('div', { class: 'body' },
        el('div', { class: 'field' }, el('label', {}, '归属 chat_id'), chatInput),
        el('div', { class: 'field' }, el('label', {}, 'RSS 链接'), urlInput),
      ),
      el('footer', {},
        el('button', { class: 'btn', onClick: close }, '取消'),
        el('button', {
          class: 'btn primary',
          onClick: async () => {
            try {
              const result = await api.post('/api/rss/feeds', { chat_id: chatInput.value.trim(), url: urlInput.value.trim() });
              toast(`已订阅「${result.title}」`, 'ok');
              close();
              reload();
            } catch (err) { toast(err.message, 'error'); }
          },
        }, '添加'),
      ),
    ));
    mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
    document.body.appendChild(mask);
    chatInput.focus();
  }
}

/* -------------------------------------------------------------- TG 监听 */

async function renderTg(container) {
  const reload = () => {
    clear(container);
    renderTg(container);
  };
  container.appendChild(spinner());
  let data;
  let discovered = { items: [] };
  try {
    [data, discovered] = await Promise.all([
      api.get('/api/tg-monitors'),
      api.get('/api/tg-discovered?limit=30').catch(() => ({ items: [] })),
    ]);
  } catch (err) {
    clear(container);
    container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
    return;
  }
  clear(container);

  const tableCard = el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h3', {}, 'TG 群监听', el('span', { class: 'sub' }, `共 ${data.items.length} 个`)),
      el('button', { class: 'btn sm primary', onClick: () => openForm(null) }, '新增监听'),
    ),
    table({
      columns: [
        { title: '名称', render: (row) => row.name },
        { title: 'chat_id', render: (row) => el('span', { class: 'mono' }, row.chat_id) },
        { title: '群标题', render: (row) => row.chat_title || '-' },
        { title: '监听模式', render: (row) => badge(row.listen_source === 'bot' ? 'Bot' : '用户会话', row.listen_source === 'bot' ? '' : 'accent') },
        { title: '关键词', render: (row) => (row.keywords && row.keywords.length ? chips(row.keywords) : el('span', { class: 'muted small' }, '（全部）')) },
        { title: '排除词', render: (row) => (row.exclude_keywords && row.exclude_keywords.length ? chips(row.exclude_keywords) : '-') },
        { title: '间隔/去重', render: (row) => `${row.min_interval_seconds}s / ${row.dedupe_window_seconds}s` },
        {
          title: '启用',
          render: (row) => switchBox({
            checked: !!row.enabled,
            onChange: async (checked) => {
              try {
                await api.put(`/api/tg-monitors/${row.id}`, { enabled: checked });
                toast('已更新', 'ok');
              } catch (err) { toast(err.message, 'error'); }
            },
          }),
        },
        {
          title: '',
          className: 'actions',
          render: (row) => el('div', { style: { display: 'flex', gap: '6px', justifyContent: 'flex-end' } },
            el('button', { class: 'btn sm', onClick: () => openForm(row) }, '编辑'),
            el('button', {
              class: 'btn sm danger',
              onClick: async () => {
                if (!(await confirmDialog({ title: '删除监听', message: `确定删除「${row.name}」吗？`, confirmText: '删除' }))) return;
                try {
                  await api.del(`/api/tg-monitors/${row.id}`);
                  toast('已删除', 'ok');
                  reload();
                } catch (err) { toast(err.message, 'error'); }
              },
            }, '删除'),
          ),
        },
      ],
      rows: data.items,
      empty: '还没有监听规则。',
    }),
  );
  container.appendChild(tableCard);

  container.appendChild(el('div', { class: 'card' },
    el('h3', {}, '最近发现的群聊', el('span', { class: 'sub' }, '来自消息监听，可用于填写 chat_id')),
    table({
      columns: [
        { title: '标题', render: (row) => row.title || '-' },
        { title: 'chat_id', render: (row) => el('span', { class: 'mono' }, row.chat_id) },
        { title: '用户名', render: (row) => (row.username ? `@${row.username}` : '-') },
        { title: '发现时间', render: (row) => fmtTime(row.discovered_at || row.created_at) },
        {
          title: '',
          className: 'actions',
          render: (row) => el('button', {
            class: 'btn sm',
            onClick: () => openForm(null, { chat_id: row.chat_id, chat_title: row.title || '' }),
          }, '用它新建'),
        },
      ],
      rows: discovered.items || [],
      empty: '暂无记录。',
    }),
  ));

  function openForm(monitor, preset = {}) {
    const nameInput = el('input', { class: 'input', value: monitor ? monitor.name : '', placeholder: '监听名称' });
    const chatIdInput = el('input', { class: 'input', value: monitor ? monitor.chat_id : (preset.chat_id || ''), placeholder: '群 chat_id（-100 开头）' });
    const chatTitleInput = el('input', { class: 'input', value: monitor ? (monitor.chat_title || '') : (preset.chat_title || ''), placeholder: '群标题（可留空）' });
    const keywordsInput = el('input', { class: 'input', value: monitor ? (monitor.keywords || []).join(', ') : '', placeholder: '关键词，逗号分隔；留空表示全部' });
    const excludeInput = el('input', { class: 'input', value: monitor ? (monitor.exclude_keywords || []).join(', ') : '', placeholder: '排除词，逗号分隔（可留空）' });
    const sourceSelect = el('select', { class: 'input' },
      el('option', { value: 'user_session' }, '用户会话（需 Telethon）'),
      el('option', { value: 'bot' }, 'Bot（仅可见消息）'),
    );
    sourceSelect.value = monitor ? monitor.listen_source : 'user_session';
    const minIntervalInput = el('input', { class: 'input', type: 'number', value: String(monitor ? monitor.min_interval_seconds : 30) });
    const dedupeInput = el('input', { class: 'input', type: 'number', value: String(monitor ? monitor.dedupe_window_seconds : 300) });
    const push = pushFields(monitor);

    const mask = el('div', { class: 'modal-mask' });
    const close = () => mask.remove();
    mask.appendChild(el('div', { class: 'modal wide' },
      el('header', {}, monitor ? '编辑监听' : '新增 TG 监听'),
      el('div', { class: 'body' },
        el('div', { class: 'field' }, el('label', {}, '名称'), nameInput),
        el('div', { class: 'row' },
          el('div', { class: 'field grow' }, el('label', {}, 'chat_id'), chatIdInput),
          el('div', { class: 'field grow' }, el('label', {}, '群标题'), chatTitleInput),
        ),
        el('div', { class: 'field' }, el('label', {}, '关键词'), keywordsInput),
        el('div', { class: 'field' }, el('label', {}, '排除词'), excludeInput),
        el('div', { class: 'field' }, el('label', {}, '监听模式'), sourceSelect),
        el('div', { class: 'row' },
          el('div', { class: 'field grow' }, el('label', {}, '最小推送间隔（秒）'), minIntervalInput),
          el('div', { class: 'field grow' }, el('label', {}, '去重窗口（秒）'), dedupeInput),
        ),
        push.nodes,
      ),
      el('footer', {},
        el('button', { class: 'btn', onClick: close }, '取消'),
        el('button', {
          class: 'btn primary',
          onClick: async () => {
            const payload = {
              name: nameInput.value.trim(),
              chat_id: Number(chatIdInput.value.trim()),
              chat_title: chatTitleInput.value.trim(),
              keywords: keywordsInput.value,
              exclude_keywords: excludeInput.value,
              listen_source: sourceSelect.value,
              min_interval_seconds: Number(minIntervalInput.value || 30),
              dedupe_window_seconds: Number(dedupeInput.value || 300),
              ...push.values(),
            };
            if (!payload.name || !payload.chat_id) {
              toast('名称与 chat_id 不能为空', 'error');
              return;
            }
            try {
              if (monitor) await api.put(`/api/tg-monitors/${monitor.id}`, payload);
              else await api.post('/api/tg-monitors', payload);
              toast('已保存', 'ok');
              close();
              reload();
            } catch (err) { toast(err.message, 'error'); }
          },
        }, '保存'),
      ),
    ));
    mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
    document.body.appendChild(mask);
    nameInput.focus();
  }
}

/* ------------------------------------------------------------ 网页监控 */

async function renderWeb(container) {
  const reload = () => {
    clear(container);
    renderWeb(container);
  };
  container.appendChild(spinner());
  let data;
  try {
    data = await api.get('/api/web-monitors');
  } catch (err) {
    clear(container);
    container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
    return;
  }
  clear(container);

  container.appendChild(el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h3', {}, '网页监控', el('span', { class: 'sub' }, `共 ${data.items.length} 个`)),
      el('button', { class: 'btn sm primary', onClick: () => openForm(null) }, '新增监控'),
    ),
    table({
      columns: [
        { title: '名称', render: (row) => row.name },
        { title: '网址', render: (row) => el('a', { href: row.url, target: '_blank', rel: 'noopener', class: 'small' }, row.url.slice(0, 55)) },
        { title: '关键词', render: (row) => (row.keywords && row.keywords.length ? chips(row.keywords) : el('span', { class: 'muted small' }, '（新条目）')) },
        { title: '间隔', render: (row) => `${row.interval_seconds}s` },
        {
          title: '最近检查',
          render: (row) => el('div', {}, fmtTime(row.last_checked_at),
            row.last_error ? el('div', { class: 'small', style: { color: 'var(--danger, #c0392b)' } }, row.last_error.slice(0, 80)) : null),
        },
        { title: '推送', render: (row) => (row.notify_telegram ? badge('开', 'ok') : badge('关', '')) },
        {
          title: '启用',
          render: (row) => switchBox({
            checked: !!row.enabled,
            onChange: async (checked) => {
              try {
                await api.put(`/api/web-monitors/${row.id}`, { enabled: checked });
                toast('已更新', 'ok');
              } catch (err) { toast(err.message, 'error'); }
            },
          }),
        },
        {
          title: '',
          className: 'actions',
          render: (row) => el('div', { style: { display: 'flex', gap: '6px', justifyContent: 'flex-end', flexWrap: 'wrap' } },
            runButton(row, reload),
            el('button', { class: 'btn sm', onClick: () => openForm(row) }, '编辑'),
            el('button', {
              class: 'btn sm danger',
              onClick: async () => {
                if (!(await confirmDialog({ title: '删除监控', message: `确定删除「${row.name}」吗？`, confirmText: '删除' }))) return;
                try {
                  await api.del(`/api/web-monitors/${row.id}`);
                  toast('已删除', 'ok');
                  reload();
                } catch (err) { toast(err.message, 'error'); }
              },
            }, '删除'),
          ),
        },
      ],
      rows: data.items,
      empty: '还没有网页监控。',
    }),
  ));

  function openForm(monitor) {
    const nameInput = el('input', { class: 'input', value: monitor ? monitor.name : '', placeholder: '监控名称' });
    const urlInput = el('input', { class: 'input', value: monitor ? monitor.url : '', placeholder: 'https://example.com/list' });
    const keywordsInput = el('input', { class: 'input', value: monitor ? (monitor.keywords || []).join(', ') : '', placeholder: '关键词，逗号分隔（可留空）' });
    const intervalInput = el('input', { class: 'input', type: 'number', value: String(monitor ? monitor.interval_seconds : 300) });
    const itemSelector = el('input', { class: 'input', value: monitor ? monitor.item_selector : '', placeholder: '默认 article, .thread, .post, li' });
    const titleSelector = el('input', { class: 'input', value: monitor ? monitor.title_selector : '', placeholder: '默认 h1, h2, h3, a' });
    const linkSelector = el('input', { class: 'input', value: monitor ? monitor.link_selector : '', placeholder: '默认 a' });
    const priceSelector = el('input', { class: 'input', value: monitor ? monitor.price_selector : '', placeholder: '价格元素选择器（可留空）' });
    const stockSelector = el('input', { class: 'input', value: monitor ? monitor.stock_selector : '', placeholder: '库存元素选择器（可留空）' });
    const push = pushFields(monitor);

    const mask = el('div', { class: 'modal-mask' });
    const close = () => mask.remove();
    mask.appendChild(el('div', { class: 'modal wide' },
      el('header', {}, monitor ? '编辑网页监控' : '新增网页监控'),
      el('div', { class: 'body' },
        el('div', { class: 'field' }, el('label', {}, '名称'), nameInput),
        el('div', { class: 'field' }, el('label', {}, '网址'), urlInput),
        el('div', { class: 'row' },
          el('div', { class: 'field grow' }, el('label', {}, '关键词（命中即提醒）'), keywordsInput),
          el('div', { class: 'field', style: { width: '150px' } }, el('label', {}, '检查间隔（秒）'), intervalInput),
        ),
        el('details', {},
          el('summary', { class: 'small muted', style: { cursor: 'pointer', marginBottom: '8px' } }, 'CSS 选择器（高级）'),
          el('div', { class: 'field' }, el('label', {}, '条目选择器'), itemSelector),
          el('div', { class: 'field' }, el('label', {}, '标题选择器'), titleSelector),
          el('div', { class: 'field' }, el('label', {}, '链接选择器'), linkSelector),
          el('div', { class: 'row' },
            el('div', { class: 'field grow' }, el('label', {}, '价格选择器'), priceSelector),
            el('div', { class: 'field grow' }, el('label', {}, '库存选择器'), stockSelector),
          ),
        ),
        push.nodes,
      ),
      el('footer', {},
        el('button', { class: 'btn', onClick: close }, '取消'),
        el('button', {
          class: 'btn primary',
          onClick: async () => {
            const payload = {
              name: nameInput.value.trim(),
              url: urlInput.value.trim(),
              keywords: keywordsInput.value,
              interval_seconds: Number(intervalInput.value || 300),
              item_selector: itemSelector.value.trim(),
              title_selector: titleSelector.value.trim(),
              link_selector: linkSelector.value.trim(),
              price_selector: priceSelector.value.trim(),
              stock_selector: stockSelector.value.trim(),
              ...push.values(),
            };
            if (!payload.name || !payload.url) {
              toast('名称与网址不能为空', 'error');
              return;
            }
            try {
              if (monitor) await api.put(`/api/web-monitors/${monitor.id}`, payload);
              else await api.post('/api/web-monitors', payload);
              toast('已保存', 'ok');
              close();
              reload();
            } catch (err) { toast(err.message, 'error'); }
          },
        }, '保存'),
      ),
    ));
    mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
    document.body.appendChild(mask);
    nameInput.focus();
  }
}

/* ------------------------------------------------------------ 补货监控 */

function stockBadge(row) {
  const [text, kind] = STOCK_STATES[row.stock_state] || ['未判断', ''];
  return badge(text, kind);
}

async function renderStock(container) {
  const reload = () => {
    clear(container);
    renderStock(container);
  };
  container.appendChild(spinner());
  let data;
  try {
    data = await api.get('/api/web-monitors?type=stock');
  } catch (err) {
    clear(container);
    container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
    return;
  }
  clear(container);

  container.appendChild(el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h3', {}, '补货监控', el('span', { class: 'sub' }, `共 ${data.items.length} 个`)),
      el('button', { class: 'btn sm primary', onClick: () => openForm(null) }, '新增商品'),
    ),
    el('div', { class: 'small muted', style: { marginBottom: '8px' } }, '从缺货变成有货时由机器人推送。只能读取服务器直接返回的网页内容，靠浏览器脚本加载库存的页面判断不了。'),
    table({
      columns: [
        { title: '商品', render: (row) => el('div', {}, row.name, row.stock_title ? el('div', { class: 'muted small' }, row.stock_title.slice(0, 60)) : null) },
        { title: '状态', render: stockBadge },
        { title: '价格', render: (row) => row.stock_price || '-' },
        { title: '间隔', render: (row) => `${Math.round(row.interval_seconds / 60)} 分钟` },
        {
          title: '最近检查',
          render: (row) => el('div', {}, fmtTime(row.last_checked_at),
            row.last_error ? el('div', { class: 'small', style: { color: 'var(--danger, #c0392b)' } }, row.last_error.slice(0, 80)) : null),
        },
        {
          title: '启用',
          render: (row) => switchBox({
            checked: !!row.enabled,
            onChange: async (checked) => {
              try {
                await api.put(`/api/web-monitors/${row.id}`, { enabled: checked });
                toast('已更新', 'ok');
              } catch (err) { toast(err.message, 'error'); }
            },
          }),
        },
        {
          title: '',
          className: 'actions',
          render: (row) => el('div', { style: { display: 'flex', gap: '6px', justifyContent: 'flex-end', flexWrap: 'wrap' } },
            runButton(row, reload),
            el('button', { class: 'btn sm', onClick: () => openForm(row) }, '编辑'),
            el('button', {
              class: 'btn sm danger',
              onClick: async () => {
                if (!(await confirmDialog({ title: '删除补货监控', message: `确定删除「${row.name}」吗？`, confirmText: '删除' }))) return;
                try {
                  await api.del(`/api/web-monitors/${row.id}`);
                  toast('已删除', 'ok');
                  reload();
                } catch (err) { toast(err.message, 'error'); }
              },
            }, '删除'),
          ),
        },
      ],
      rows: data.items,
      empty: '还没有补货监控，点「新增商品」填上商品网址就行。',
    }),
  ));

  function openForm(monitor) {
    const value = (key) => (monitor && monitor[key]) || '';
    const nameInput = el('input', { class: 'input', value: value('name'), placeholder: '比如 显卡 RTX' });
    const urlInput = el('input', { class: 'input', type: 'url', value: value('url'), placeholder: 'https://shop.example.com/item/123' });
    const keywordInput = el('input', { class: 'input', value: value('product_keyword'), placeholder: '列表页才需要，填商品名里的关键词' });
    const intervalInput = el('input', { class: 'input', type: 'number', min: '1', value: String(monitor ? Math.round(monitor.interval_seconds / 60) : 5) });
    const stockSelector = el('input', { class: 'input', value: value('stock_selector'), placeholder: '例如 .stock-status，判断不准时再填' });
    const priceSelector = el('input', { class: 'input', value: value('price_selector'), placeholder: '例如 .price（可留空）' });
    const inWords = el('input', { class: 'input', value: value('in_stock_words'), placeholder: '留空用默认：有货、现货、加入购物车、in stock……' });
    const outWords = el('input', { class: 'input', value: value('out_of_stock_words'), placeholder: '留空用默认：缺货、售罄、无货、sold out……' });
    const push = pushFields(monitor);
    const resultNode = el('div', { class: 'small', style: { marginTop: '8px' } });

    const formValues = () => ({
      name: nameInput.value.trim(),
      url: urlInput.value.trim(),
      product_keyword: keywordInput.value.trim(),
      stock_selector: stockSelector.value.trim(),
      price_selector: priceSelector.value.trim(),
      in_stock_words: inWords.value.trim(),
      out_of_stock_words: outWords.value.trim(),
    });

    const testButton = el('button', { class: 'btn' }, '先试一次');
    testButton.addEventListener('click', async () => {
      testButton.disabled = true;
      resultNode.textContent = '正在打开页面……';
      try {
        const result = await api.post('/api/web-monitors/preview', formValues());
        const [text] = STOCK_STATES[result.state] || ['判断不出来'];
        resultNode.textContent = [`结果：${text}`, result.title, result.price, result.evidence].filter(Boolean).join(' · ');
      } catch (err) {
        resultNode.textContent = err.message;
      } finally {
        testButton.disabled = false;
      }
    });

    const mask = el('div', { class: 'modal-mask' });
    const close = () => mask.remove();
    mask.appendChild(el('div', { class: 'modal wide' },
      el('header', {}, monitor ? '编辑补货监控' : '新增补货监控'),
      el('div', { class: 'body' },
        el('div', { class: 'field' }, el('label', {}, '名称'), nameInput),
        el('div', { class: 'field' }, el('label', {}, '商品网址'), urlInput),
        el('div', { class: 'row' },
          el('div', { class: 'field grow' }, el('label', {}, '商品关键词'), keywordInput),
          el('div', { class: 'field', style: { width: '130px' } }, el('label', {}, '检查间隔（分钟）'), intervalInput),
        ),
        el('details', {},
          el('summary', { class: 'small muted', style: { cursor: 'pointer', marginBottom: '8px' } }, '判断规则（高级）'),
          el('div', { class: 'field' }, el('label', {}, '库存选择器'), stockSelector),
          el('div', { class: 'field' }, el('label', {}, '价格选择器'), priceSelector),
          el('div', { class: 'field' }, el('label', {}, '有货字样（逗号分隔）'), inWords),
          el('div', { class: 'field' }, el('label', {}, '缺货字样（逗号分隔）'), outWords),
        ),
        push.nodes,
        resultNode,
      ),
      el('footer', {},
        el('button', { class: 'btn', onClick: close }, '取消'),
        testButton,
        el('button', {
          class: 'btn primary',
          onClick: async () => {
            const payload = {
              ...formValues(),
              ...push.values(),
              monitor_type: 'stock',
              interval_seconds: Math.max(1, Number(intervalInput.value || 5)) * 60,
            };
            if (!payload.name || !payload.url) {
              toast('名称与商品网址不能为空', 'error');
              return;
            }
            try {
              if (monitor) await api.put(`/api/web-monitors/${monitor.id}`, payload);
              else await api.post('/api/web-monitors', payload);
              toast('已保存', 'ok');
              close();
              reload();
            } catch (err) { toast(err.message, 'error'); }
          },
        }, '保存'),
      ),
    ));
    mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
    document.body.appendChild(mask);
    nameInput.focus();
  }
}

/* ------------------------------------------------------------ 监控结果 */

async function renderEvents(container, filter = { source: '', page: 1 }) {
  const reload = (next) => {
    clear(container);
    renderEvents(container, { ...filter, ...next });
  };
  container.appendChild(spinner());
  let data;
  try {
    const params = new URLSearchParams({ page: String(filter.page), per_page: String(EVENT_PAGE_SIZE) });
    if (filter.source) params.set('source', filter.source);
    data = await api.get(`/api/monitor-events?${params}`);
  } catch (err) {
    clear(container);
    container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
    return;
  }
  clear(container);

  const sourceSelect = selectInput({
    options: [{ value: '', label: '全部来源' }, ...Object.entries(EVENT_SOURCES).map(([value, [label]]) => ({ value, label }))],
    value: filter.source,
    onChange: (source) => reload({ source, page: 1 }),
  });
  const testButton = el('button', { class: 'btn sm' }, '测试推送');
  testButton.addEventListener('click', async () => {
    try {
      const result = await api.post('/api/monitor-push-test');
      toast(`已发送 ${result.sent}/${result.total} 条测试消息`, result.sent ? 'ok' : 'error');
    } catch (err) { toast(err.message, 'error'); }
  });

  container.appendChild(el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h3', {}, '监控结果', el('span', { class: 'sub' }, `共 ${data.total} 条`)),
      el('div', { class: 'row' }, sourceSelect, testButton),
    ),
    table({
      columns: [
        { title: '时间', render: (row) => fmtTime(row.created_at) },
        {
          title: '来源',
          render: (row) => {
            const [text, kind] = EVENT_SOURCES[row.source] || [row.source, ''];
            return badge(text, kind);
          },
        },
        { title: '监控', render: (row) => row.monitor_name || '-' },
        {
          title: '内容',
          render: (row) => el('div', { class: 'match-cell' },
            row.link ? el('a', { href: row.link, target: '_blank', rel: 'noopener' }, row.title || row.link) : (row.title || '-'),
            row.detail ? el('div', { class: 'muted small' }, row.detail.slice(0, 200)) : null,
          ),
        },
        { title: '推送', render: (row) => (row.pushed_count ? `${row.pushed_count} 条` : '未推送') },
      ],
      rows: data.items,
      empty: '还没有监控结果，命中后会出现在这里。',
    }),
    pager({
      page: data.page,
      totalPages: data.total_pages,
      total: data.total,
      onChange: (page) => reload({ page }),
    }),
  ));
}

export default {
  title: 'RSS / 监控',
  render(container) {
    const body = el('div', {});
    container.appendChild(tabsBar(() => load()));
    container.appendChild(body);

    function load() {
      clear(body);
      if (state.tab === 'rss') return renderRss(body);
      if (state.tab === 'tg') return renderTg(body);
      if (state.tab === 'stock') return renderStock(body);
      if (state.tab === 'events') return renderEvents(body);
      return renderWeb(body);
    }

    load();
  },
};
