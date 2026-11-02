/* 网络工具：通过 SSH 远程执行 Ping / NextTrace，管理测试服务器。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, confirmDialog, table } from '../ui.js';

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

export default {
  title: '网络工具',
  render(container) {
    renderNetwork(container);
  },
};
