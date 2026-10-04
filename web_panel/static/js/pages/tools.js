/* 系统工具：SSH 网络测试（Ping / NextTrace）与更新（Git / 镜像）。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, confirmDialog, table, fmtTime } from '../ui.js';

const state = { tab: 'network' };

function tabsBar(onChange) {
  const tabs = [
    ['network', '网络工具'],
    ['update', '更新部署'],
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

/* ---------------------------------------------------------------- 网络 */

async function renderNetwork(container) {
  const reload = () => {
    clear(container);
    renderNetwork(container);
  };
  container.appendChild(spinner());

  let servers;
  try {
    servers = (await api.get('/api/network/servers')).items;
  } catch (err) {
    clear(container);
    container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
    return;
  }
  clear(container);

  const resultBox = el('pre', { class: 'result', style: { display: 'none' } });
  const serverSelect = el('select', { class: 'input' },
    ...servers.map((server) => el('option', { value: server.name }, `${server.name}（${server.host}:${server.port}）`)),
  );
  const targetInput = el('input', { class: 'input', placeholder: '目标 IP 或域名，如 8.8.8.8' });
  const countInput = el('input', { class: 'input', type: 'number', value: '4', min: '1', max: '10', style: { width: '90px' } });
  const ipTypeSelect = el('select', { class: 'input', style: { width: '110px' } },
    el('option', { value: 'ipv4' }, 'IPv4'),
    el('option', { value: 'ipv6' }, 'IPv6'),
  );
  const modeSelect = el('select', { class: 'input', style: { width: '110px' } },
    el('option', { value: 'icmp' }, 'ICMP'),
    el('option', { value: 'tcp' }, 'TCP'),
    el('option', { value: 'udp' }, 'UDP'),
  );

  async function run(button, path, payload, label) {
    if (!servers.length) {
      toast('请先添加一台 SSH 服务器', 'error');
      return;
    }
    if (!targetInput.value.trim()) {
      toast('请输入目标地址', 'error');
      return;
    }
    const original = button.textContent;
    button.disabled = true;
    button.textContent = '执行中……';
    resultBox.style.display = 'block';
    resultBox.textContent = `${label}执行中，请稍候……`;
    try {
      const data = await api.post(path, payload);
      resultBox.textContent = data.result || '（无输出）';
    } catch (err) {
      resultBox.textContent = `执行失败：${err.message}`;
      toast(err.message, 'error');
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  const pingButton = el('button', { class: 'btn primary' }, 'Ping');
  pingButton.addEventListener('click', () => run(pingButton, '/api/network/ping', {
    server: serverSelect.value,
    target: targetInput.value.trim(),
    count: Number(countInput.value || 4),
  }, 'Ping '));

  const traceButton = el('button', { class: 'btn' }, 'NextTrace');
  traceButton.addEventListener('click', () => run(traceButton, '/api/network/nexttrace', {
    server: serverSelect.value,
    target: targetInput.value.trim(),
    ip_type: ipTypeSelect.value,
    mode: modeSelect.value,
  }, 'NextTrace '));

  const installButton = el('button', { class: 'btn' }, '安装 NextTrace');
  installButton.addEventListener('click', async () => {
    if (!servers.length) return;
    if (!(await confirmDialog({ title: '安装 NextTrace', message: `确定在「${serverSelect.value}」上安装/更新 NextTrace 吗？`, confirmText: '安装', danger: false }))) return;
    installButton.disabled = true;
    installButton.textContent = '安装中……';
    resultBox.style.display = 'block';
    resultBox.textContent = '正在安装，请稍候……';
    try {
      const data = await api.post('/api/network/install', { server: serverSelect.value });
      resultBox.textContent = data.result || '（无输出）';
    } catch (err) {
      resultBox.textContent = `安装失败：${err.message}`;
      toast(err.message, 'error');
    } finally {
      installButton.disabled = false;
      installButton.textContent = '安装 NextTrace';
    }
  });

  container.appendChild(el('div', { class: 'card' },
    el('h3', {}, '远程网络测试', el('span', { class: 'sub' }, '通过 SSH 在指定服务器上执行')),
    el('div', { class: 'row' },
      el('div', { style: { width: '220px' } }, el('label', { class: 'small muted' }, '服务器'), serverSelect),
      el('div', { class: 'grow' }, el('label', { class: 'small muted' }, '目标'), targetInput),
    ),
    el('div', { class: 'row', style: { marginTop: '10px' } },
      el('div', {}, el('label', { class: 'small muted' }, 'Ping 次数'), countInput),
      el('div', {}, el('label', { class: 'small muted' }, 'IP 类型'), ipTypeSelect),
      el('div', {}, el('label', { class: 'small muted' }, '模式'), modeSelect),
      el('div', { style: { display: 'flex', gap: '8px', paddingBottom: '2px' } }, pingButton, traceButton, installButton),
    ),
    el('div', { style: { marginTop: '12px' } }, resultBox),
  ));

  // 服务器管理
  const nameInput = el('input', { class: 'input', placeholder: '名称' });
  const hostInput = el('input', { class: 'input', placeholder: '主机（IP/域名）' });
  const portInput = el('input', { class: 'input', type: 'number', value: '22', placeholder: '端口' });
  const userInput = el('input', { class: 'input', placeholder: 'SSH 用户名' });
  const passInput = el('input', { class: 'input', type: 'password', placeholder: 'SSH 密码' });

  container.appendChild(el('div', { class: 'card' },
    el('div', { class: 'card-head' }, el('h3', {}, 'SSH 服务器')),
    table({
      columns: [
        { title: '名称', render: (row) => row.name },
        { title: '主机', render: (row) => el('span', { class: 'mono' }, `${row.host}:${row.port}`) },
        { title: '用户名', render: (row) => row.username },
        {
          title: '',
          className: 'actions',
          render: (row) => el('button', {
            class: 'btn sm danger',
            onClick: async () => {
              if (!(await confirmDialog({ title: '删除服务器', message: `确定删除「${row.name}」吗？`, confirmText: '删除' }))) return;
              try {
                await api.del(`/api/network/servers/${encodeURIComponent(row.name)}`);
                toast('已删除', 'ok');
                reload();
              } catch (err) { toast(err.message, 'error'); }
            },
          }, '删除'),
        },
      ],
      rows: servers,
      empty: '还没有登记 SSH 服务器。',
    }),
    el('div', { class: 'row', style: { marginTop: '12px' } },
      el('div', { class: 'grow' }, nameInput),
      el('div', { class: 'grow' }, hostInput),
      el('div', { style: { width: '90px' } }, portInput),
      el('div', { class: 'grow' }, userInput),
      el('div', { class: 'grow' }, passInput),
      el('button', {
        class: 'btn sm primary',
        onClick: async () => {
          try {
            await api.post('/api/network/servers', {
              name: nameInput.value.trim(),
              host: hostInput.value.trim(),
              port: Number(portInput.value || 22),
              username: userInput.value.trim(),
              password: passInput.value,
            });
            toast('服务器已登记', 'ok');
            reload();
          } catch (err) { toast(err.message, 'error'); }
        },
      }, '登记'),
    ),
  ));
}

/* ---------------------------------------------------------------- 更新 */

function statusRows(pairs) {
  return el('div', { class: 'small' },
    pairs.filter(Boolean).map(([label, value]) => el('div', { style: { display: 'flex', gap: '8px' } },
      el('span', { class: 'muted', style: { minWidth: '110px' } }, label),
      el('span', { class: 'mono' }, value === null || value === undefined || value === '' ? '-' : String(value)),
    )),
  );
}

async function renderUpdate(container) {
  container.appendChild(el('div', { class: 'card' }, spinner()));

  async function load() {
    clear(container);
    const gitCard = el('div', { class: 'card' });
    const imageCard = el('div', { class: 'card' });
    container.appendChild(gitCard);
    container.appendChild(imageCard);

    /* --- Git --- */
    gitCard.appendChild(spinner());
    try {
      const data = await api.get('/api/update/git');
      const status = data.status;
      clear(gitCard);
      gitCard.appendChild(el('div', { class: 'card-head' },
        el('h3', {}, 'Git 更新'),
        el('div', { style: { display: 'flex', gap: '8px' } },
          el('button', { class: 'btn sm', onClick: load }, '刷新'),
          el('button', {
            class: 'btn sm primary',
            onClick: async () => {
              if (!(await confirmDialog({ title: '拉取更新', message: '将从远端拉取最新代码（ff-only）。更新后建议重启容器/进程。确定继续吗？', confirmText: '拉取' }))) return;
              try {
                const result = await api.post('/api/update/git');
                toast(`已更新到 ${result.status.head.slice(0, 12)}`, 'ok');
                load();
              } catch (err) { toast(err.message, 'error'); }
            },
          }, '拉取更新'),
          el('button', {
            class: 'btn sm danger',
            onClick: async () => {
              if (!(await confirmDialog({ title: '回滚更新', message: '将回滚到上一次更新前的提交。确定继续吗？', confirmText: '回滚' }))) return;
              try {
                const result = await api.post('/api/update/git/rollback');
                toast(result.message || '已回滚', 'ok');
                load();
              } catch (err) { toast(err.message, 'error'); }
            },
          }, '回滚'),
        ),
      ));
      gitCard.appendChild(statusRows([
        ['分支', status.branch],
        ['本地 HEAD', status.head],
        ['远端 HEAD', status.remote_head],
        ['落后/领先', `${status.behind} / ${status.ahead}`],
        ['工作区', status.dirty ? '有未提交改动' : '干净'],
        ['上次回滚点', data.rollback || '-'],
      ]));
      if (status.behind > 0) {
        gitCard.appendChild(el('div', { style: { marginTop: '8px' } }, badge(`有 ${status.behind} 个新提交可拉取`, 'warn')));
      }
    } catch (err) {
      clear(gitCard);
      gitCard.appendChild(el('div', {}, `Git 状态获取失败：${err.message}`));
    }

    /* --- 镜像 --- */
    imageCard.appendChild(spinner());
    try {
      const data = await api.get('/api/update/image');
      const status = data.status;
      clear(imageCard);
      imageCard.appendChild(el('div', { class: 'card-head' },
        el('h3', {}, '镜像更新'),
        el('div', { style: { display: 'flex', gap: '8px' } },
          el('button', { class: 'btn sm', onClick: load }, '刷新'),
          el('button', {
            class: 'btn sm primary',
            onClick: async () => {
              if (!(await confirmDialog({ title: '触发镜像更新', message: '将通过 Watchtower 拉取新镜像并重建容器，面板会短暂断开。确定继续吗？', confirmText: '触发更新' }))) return;
              try {
                const result = await api.post('/api/update/image');
                toast(result.message || '已触发更新', 'ok');
              } catch (err) { toast(err.message, 'error'); }
            },
          }, '触发更新'),
        ),
      ));
      imageCard.appendChild(statusRows([
        ['运行环境', status.in_container ? 'Docker 容器' : '非容器环境'],
        ['镜像', status.image],
        ['本地构建', status.local_sha || '-'],
        ['远端摘要', status.remote_digest || '-'],
        ['已知摘要', status.known_digest || '-'],
        ['错误', status.error || '-'],
      ]));
      imageCard.appendChild(el('div', { style: { marginTop: '8px' } },
        status.error
          ? badge('检查未完成', 'warn')
          : (status.update_available ? badge('发现新版本', 'warn') : badge('已是最新', 'ok')),
      ));
    } catch (err) {
      clear(imageCard);
      imageCard.appendChild(el('div', {}, `镜像状态获取失败：${err.message}`));
    }
  }

  load();
}

export default {
  title: '网络与更新',
  render(container) {
    const body = el('div', {});
    container.appendChild(tabsBar(() => load()));
    container.appendChild(body);

    function load() {
      clear(body);
      if (state.tab === 'network') return renderNetwork(body);
      return renderUpdate(body);
    }

    load();
  },
};
