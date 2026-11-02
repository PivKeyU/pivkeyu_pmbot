/* 更新部署：当前版本、自动更新，详细信息和另一种更新方式默认收起。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, confirmDialog, fmtTime, switchBox } from '../ui.js';

const HASH_LENGTH = 12;
const HASH_PATTERN = /(sha256:)?([0-9a-f]{16,})$/i;

function emptyToDash(value) {
  return value === null || value === undefined || value === '' ? '-' : value;
}

/* 长哈希只显示前几位，点一下复制完整值 */
function hashValue(value) {
  const text = String(emptyToDash(value));
  const match = text.match(HASH_PATTERN);
  if (!match) return text;
  const short = `${match[1] || ''}${match[2].slice(0, HASH_LENGTH)}`;
  return el('button', {
    class: 'hash-chip',
    title: `${text}（点击复制）`,
    onClick: async () => {
      try {
        await navigator.clipboard.writeText(text);
        toast('已复制完整值', 'ok');
      } catch (err) {
        toast(text, 'info', 8000);
      }
    },
  }, short);
}

function infoRows(pairs) {
  return el('div', { class: 'info-rows' }, pairs.filter(Boolean).flatMap(([label, value]) => [
    el('span', { class: 'muted' }, label),
    el('span', { class: 'info-value' }, value instanceof Node ? value : String(emptyToDash(value))),
  ]));
}

function foldCard(title, ...content) {
  return el('details', { class: 'card fold-card' }, el('summary', {}, title), el('div', { class: 'fold-body' }, content));
}

function utcLabel(minutes) {
  const sign = minutes < 0 ? '-' : '+';
  const absolute = Math.abs(minutes);
  const rest = absolute % 60;
  return `UTC${sign}${Math.floor(absolute / 60)}${rest ? `:${String(rest).padStart(2, '0')}` : ''}`;
}

/* ---------------------------------------------------------------- 操作 */

async function updateGit(reload) {
  if (!(await confirmDialog({ title: '拉取更新', message: '将从远端拉取最新代码（ff-only）。更新后需要重启机器人才会生效。确定继续吗？', confirmText: '拉取' }))) return;
  try {
    const result = await api.post('/api/update/git');
    toast(`已更新到 ${result.status.head.slice(0, HASH_LENGTH)}`, 'ok');
    reload();
  } catch (err) { toast(err.message, 'error'); }
}

async function rollbackGit(reload) {
  if (!(await confirmDialog({ title: '回滚更新', message: '将回滚到上一次更新前的提交。确定继续吗？', confirmText: '回滚' }))) return;
  try {
    const result = await api.post('/api/update/git/rollback');
    toast(result.message || '已回滚', 'ok');
    reload();
  } catch (err) { toast(err.message, 'error'); }
}

async function updateImage() {
  if (!(await confirmDialog({ title: '更新镜像', message: '将通过 Watchtower 拉取新镜像并重建容器，机器人和面板会短暂断开。确定继续吗？', confirmText: '开始更新' }))) return;
  try {
    const result = await api.post('/api/update/image');
    toast(result.message || '已触发更新', 'ok');
  } catch (err) { toast(err.message, 'error'); }
}

/* ---------------------------------------------------------------- 版本信息 */

function gitSummary(data) {
  const { status } = data;
  let state = badge('已是最新', 'ok');
  if (status.behind > 0) state = badge(`有 ${status.behind} 个新提交`, 'warn');
  return {
    method: 'Git 拉取',
    current: status.head,
    latest: status.remote_head,
    state,
    canUpdate: status.behind > 0,
  };
}

function imageSummary(data) {
  const { status } = data;
  let state = badge('已是最新', 'ok');
  if (status.error) state = badge('检查未完成', 'warn');
  else if (status.update_available) state = badge('发现新版本', 'warn');
  return {
    method: 'Docker 镜像',
    current: status.known_digest || status.local_sha,
    latest: status.remote_digest,
    state,
    canUpdate: Boolean(status.update_available),
  };
}

function gitDetails(data) {
  const { status } = data;
  return infoRows([
    ['分支', status.branch],
    ['本地提交', hashValue(status.head)],
    ['远端提交', hashValue(status.remote_head)],
    ['落后 / 领先', `${status.behind} / ${status.ahead}`],
    ['工作区', status.dirty ? '有未提交改动' : '干净'],
    ['回滚点', hashValue(data.rollback)],
  ]);
}

function imageDetails(data) {
  const { status } = data;
  return infoRows([
    ['运行环境', status.in_container ? 'Docker 容器' : '非容器环境'],
    ['镜像', status.image || [status.image_repo, status.image_tag].filter(Boolean).join(':')],
    ['本地构建', hashValue(status.local_sha)],
    ['远端摘要', hashValue(status.remote_digest)],
    ['已知摘要', hashValue(status.known_digest)],
    ['错误', status.error],
  ]);
}

function summaryCard(summary, onUpdate, reload) {
  const actions = [el('button', { class: 'btn sm', onClick: reload }, '重新检查')];
  if (onUpdate) {
    actions.unshift(el('button', {
      class: `btn sm ${summary.canUpdate ? 'primary' : ''}`.trim(),
      onClick: onUpdate,
    }, summary.canUpdate ? '立即更新' : '强制更新'));
  }
  return el('div', { class: 'card' },
    el('div', { class: 'card-head' }, el('h3', {}, '当前版本'), summary.state),
    infoRows([
      ['更新方式', summary.method],
      ['当前版本', hashValue(summary.current)],
      ['最新版本', hashValue(summary.latest)],
    ]),
    el('div', { class: 'update-actions' }, actions),
  );
}

/* ---------------------------------------------------------------- 自动更新 */

async function renderAutoUpdate(card, data) {
  const { settings, state: updateState } = data;
  const timeInput = el('input', { class: 'input', type: 'time', value: settings.time, style: { width: '130px' } });
  const offsetSelect = el('select', { class: 'input', style: { width: '130px' } });
  const offsets = new Set([settings.utc_offset_minutes, -new Date().getTimezoneOffset()]);
  for (let hour = -12; hour <= 14; hour += 1) offsets.add(hour * 60);
  [...offsets].sort((a, b) => a - b).forEach((minutes) => {
    offsetSelect.appendChild(el('option', { value: String(minutes) }, utcLabel(minutes)));
  });
  offsetSelect.value = String(settings.utc_offset_minutes);

  const save = async (consented) => {
    await api.put('/api/update/auto', {
      consented,
      time: timeInput.value,
      utc_offset_minutes: Number(offsetSelect.value),
    });
    clear(card);
    renderAutoUpdate(card, await api.get('/api/update/auto'));
  };

  const consentSwitch = switchBox({
    checked: settings.consented,
    onChange: async (checked) => {
      if (checked) {
        const confirmed = await confirmDialog({
          title: '同意自动更新',
          message: `同意后，发现新版本时机器人会在每天 ${timeInput.value}（${utcLabel(Number(offsetSelect.value))}）自动更新，更新期间机器人和面板会短暂断开。你可以随时关闭。确定同意吗？`,
          confirmText: '同意自动更新',
          danger: false,
        });
        if (!confirmed) return false;
      }
      await save(checked);
      toast(checked ? '已同意自动更新' : '已关闭自动更新', 'ok');
      return true;
    },
  });

  card.appendChild(el('div', { class: 'card-head' },
    el('h3', {}, '自动更新', el('span', { class: 'sub' }, '启动时和每 6 小时检测一次')),
    consentSwitch,
  ));
  card.appendChild(el('div', { class: 'muted small', style: { marginBottom: '10px' } },
    settings.consented
      ? `已于 ${fmtTime(settings.consented_at)} 同意。发现新版本后会在设定时间起的 1 小时内自动更新。`
      : '还没有同意，机器人只检测新版本并提醒，不会自动更新。'));
  card.appendChild(el('div', { class: 'row', style: { alignItems: 'center', marginBottom: '12px' } },
    el('span', { class: 'small' }, '每天'), timeInput, offsetSelect,
    el('button', {
      class: 'btn sm primary',
      onClick: async () => {
        try {
          await save(settings.consented);
          toast('更新时间已保存', 'ok');
        } catch (err) { toast(err.message, 'error'); }
      },
    }, '保存时间')));
  if (data.mode === 'image' && !data.watchtower_ready) {
    card.appendChild(el('div', { style: { marginBottom: '10px' } },
      badge('还没配置 WATCHTOWER_HTTP_API_TOKEN，镜像无法自动更新', 'warn')));
  }
  card.appendChild(infoRows([
    ['最近检测', updateState.last_checked_at ? fmtTime(updateState.last_checked_at) : '-'],
    ['检测结果', !updateState.last_checked_at ? '尚未检测' : (updateState.error || (updateState.update_available ? '发现新版本' : '已是最新'))],
    ['下次更新', data.next_update_at ? fmtTime(data.next_update_at) : '-'],
    ['上次自动更新', updateState.last_attempt_at ? `${fmtTime(updateState.last_attempt_at)}（${updateState.last_result === 'ok' ? '已触发' : (updateState.last_result || '无需更新')}）` : '-'],
  ]));
}

/* ---------------------------------------------------------------- 页面 */

async function renderPage(container) {
  const reload = () => {
    clear(container);
    renderPage(container);
  };
  container.appendChild(el('div', { class: 'card' }, spinner()));

  const safely = (path) => api.get(path).catch((err) => ({ failed: err.message }));
  const [auto, git, image] = await Promise.all([
    safely('/api/update/auto'), safely('/api/update/git'), safely('/api/update/image'),
  ]);
  clear(container);

  // 只把当前部署方式放在最前面，另一种方式收起来，避免两套按钮同时出现
  const usesImage = auto.mode ? auto.mode === 'image' : !image.failed;
  const primary = usesImage ? image : git;
  const secondary = usesImage ? git : image;
  const applyUpdate = usesImage ? () => updateImage() : () => updateGit(reload);

  if (primary.failed) {
    container.appendChild(el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h3', {}, '当前版本'), badge('获取失败', 'danger')),
      el('div', { class: 'small' }, primary.failed),
    ));
  } else {
    container.appendChild(summaryCard(usesImage ? imageSummary(primary) : gitSummary(primary), applyUpdate, reload));
  }

  const autoCard = el('div', { class: 'card' });
  container.appendChild(autoCard);
  if (auto.failed) autoCard.appendChild(el('div', {}, `自动更新状态获取失败：${auto.failed}`));
  else renderAutoUpdate(autoCard, auto);

  if (!primary.failed) {
    const rollback = !usesImage && primary.rollback
      ? el('div', { class: 'update-actions' }, el('button', { class: 'btn sm danger', onClick: () => rollbackGit(reload) }, '回滚到上一版'))
      : null;
    container.appendChild(foldCard('版本详情', usesImage ? imageDetails(primary) : gitDetails(primary), rollback));
  }

  if (!secondary.failed) {
    const details = usesImage ? gitDetails(secondary) : imageDetails(secondary);
    const action = usesImage
      ? el('button', { class: 'btn sm', onClick: () => updateGit(reload) }, '用 Git 拉取更新')
      : el('button', { class: 'btn sm', onClick: () => updateImage() }, '用 Watchtower 更新镜像');
    container.appendChild(foldCard('其他更新方式', details, el('div', { class: 'update-actions' }, action)));
  }
}

export default {
  title: '更新部署',
  render(container) {
    renderPage(container);
  },
};
