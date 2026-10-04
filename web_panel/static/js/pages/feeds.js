/* 订阅与监控：RSS 订阅 / TG 群监听 / 网页监控。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, confirmDialog, table, switchBox, chips, fmtTime } from '../ui.js';

const state = { tab: 'rss' };

function tabsBar(onChange) {
  const tabs = [
    ['rss', 'RSS 订阅'],
    ['tg', 'TG 群监听'],
    ['web', '网页监控'],
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
        { title: '最近检查', render: (row) => fmtTime(row.last_checked_at) },
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
          render: (row) => el('div', { style: { display: 'flex', gap: '6px', justifyContent: 'flex-end' } },
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
      return renderWeb(body);
    }

    load();
  },
};
