/* 网盘：文件夹浏览、上传、直链复制、预览、改名、移动与删除。面板与 Mini App 共用。 */

import { api, upload } from '../api.js';
import { el, clear, spinner, toast, confirmDialog, modal, pager, fmtTime, withLoading, textInput } from '../ui.js';

const SORTS = [
  { value: 'time', label: '最新上传' },
  { value: 'name', label: '按名称' },
  { value: 'size', label: '按大小' },
];

const LINK_FORMATS = [
  { key: 'url', label: '直链' },
  { key: 'markdown', label: 'Markdown' },
  { key: 'html', label: 'HTML' },
  { key: 'bbcode', label: 'BBCode' },
];

const STORAGE_TEXT = {
  channel: '文件存放在私有存储频道里',
  admin_chat: '文件暂存在管理员私聊里，建议配置 DRIVE_STORAGE_CHAT_ID 换成私有频道',
  none: '还没有可存放文件的地方，请先配置 ADMIN_IDS 或 DRIVE_STORAGE_CHAT_ID',
};

function fmtSize(bytes) {
  let value = Number(bytes) || 0;
  const units = ['B', 'KB', 'MB', 'GB'];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return index === 0 ? `${value} B` : `${value.toFixed(1)} ${units[index]}`;
}

function iconFor(item) {
  if (item.is_folder) return '📁';
  const mime = item.mime_type || '';
  if (mime.startsWith('image/')) return '🖼️';
  if (mime.startsWith('video/')) return '🎬';
  if (mime.startsWith('audio/')) return '🎵';
  if (mime === 'application/pdf') return '📄';
  if (/zip|rar|7z|tar|gzip/.test(mime)) return '🗜️';
  if (mime.startsWith('text/')) return '📝';
  return '📦';
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    const area = el('textarea', { class: 'drive-copy-buffer', readonly: true });
    area.value = text;
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (inner) { copied = false; }
    area.remove();
    if (!copied) {
      toast('浏览器不允许自动复制，请手动复制', 'error');
      return;
    }
  }
  toast('已复制', 'ok');
}

function folderHash(folderId) {
  return folderId ? `#/drive/${folderId}` : '#/drive';
}

function thumbNode(item) {
  const icon = el('span', { class: 'drive-icon' }, iconFor(item));
  if (item.is_folder || !item.thumb_url) return icon;
  const img = el('img', { class: 'drive-thumb', src: item.thumb_url, alt: '', loading: 'lazy' });
  img.addEventListener('error', () => img.replaceWith(icon));
  return img;
}

function previewNode(item) {
  const mime = item.mime_type || '';
  if (mime.startsWith('image/') && !mime.includes('svg')) {
    return el('img', { class: 'drive-preview', src: item.preview_url, alt: item.name });
  }
  if (mime.startsWith('video/')) {
    return el('video', { class: 'drive-preview', src: item.preview_url, controls: true, preload: 'metadata' });
  }
  if (mime.startsWith('audio/')) {
    return el('audio', { class: 'drive-preview', src: item.preview_url, controls: true, preload: 'metadata' });
  }
  return el('div', { class: 'drive-preview-empty' }, el('span', { class: 'drive-icon big' }, iconFor(item)),
    el('div', { class: 'muted small' }, '这种文件不能直接预览，可以打开或下载直链'));
}

function folderPaths(folders, excludedId) {
  const byId = new Map(folders.map((folder) => [folder.id, folder]));
  const pathOf = (folder) => {
    const parts = [];
    let current = folder;
    const seen = new Set();
    while (current) {
      if (current.id === excludedId || seen.has(current.id)) return null;
      seen.add(current.id);
      parts.unshift(current.name);
      current = byId.get(current.parent_id);
    }
    return `/${parts.join('/')}`;
  };
  return folders.map((folder) => ({ id: folder.id, path: pathOf(folder) }))
    .filter((folder) => folder.path !== null)
    .sort((a, b) => a.path.localeCompare(b.path, 'zh-CN'));
}

function askName({ title, value = '', placeholder = '', confirmText = '确定' }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      dialog.close();
      resolve(result);
    };
    const input = textInput({ value, placeholder, onEnter: (text) => finish(text.trim() || null) });
    const dialog = modal({
      title,
      body: input,
      footer: [
        el('button', { class: 'btn', onClick: () => finish(null) }, '取消'),
        el('button', { class: 'btn primary', onClick: () => finish(input.value.trim() || null) }, confirmText),
      ],
    });
    dialog.mask.addEventListener('click', (event) => { if (event.target === dialog.mask) finish(null); });
    setTimeout(() => {
      input.focus();
      const dot = value.lastIndexOf('.');
      input.setSelectionRange(0, dot > 0 ? dot : value.length);
    }, 0);
  });
}

export default {
  title: '网盘',
  render(container, params = []) {
    const parentId = Number(params[0]) || null;
    const state = { query: '', sort: 'time', page: 1, status: null };
    let generation = 0;
    let disposed = false;

    const statusCard = el('div', { class: 'card drive-status' }, spinner());
    const crumbs = el('div', { class: 'drive-crumbs' });
    const listBox = el('div', { class: 'drive-list' }, spinner());
    const pagerBox = el('div');
    const queueBox = el('div', { class: 'drive-queue' });
    const fileInput = el('input', { type: 'file', multiple: true, class: 'drive-file-input' });

    const searchInput = textInput({
      placeholder: '搜索全部文件名',
      onEnter: (text) => { state.query = text.trim(); state.page = 1; loadList(); },
    });
    const sortSelect = el('select', { class: 'input drive-sort' },
      SORTS.map((sort) => el('option', { value: sort.value }, sort.label)));
    sortSelect.addEventListener('change', () => { state.sort = sortSelect.value; state.page = 1; loadList(); });

    const uploadButton = el('button', { class: 'btn primary', disabled: true, onClick: () => fileInput.click() }, '上传文件');
    const folderButton = el('button', { class: 'btn', disabled: true, onClick: createFolder }, '新建文件夹');
    const dropHint = el('div', { class: 'drive-drop-hint muted small', hidden: true }, '可以把文件直接拖到这里上传');
    fileInput.addEventListener('change', () => {
      const files = Array.from(fileInput.files || []);
      fileInput.value = '';
      uploadFiles(files);
    });

    const dropZone = el('div', { class: 'card drive-main' },
      el('div', { class: 'drive-toolbar' },
        crumbs,
        el('div', { class: 'drive-actions' }, folderButton, uploadButton),
      ),
      el('div', { class: 'drive-toolbar' }, searchInput, sortSelect),
      dropHint,
      queueBox,
      listBox,
      pagerBox,
      fileInput,
    );

    let dragDepth = 0;
    dropZone.addEventListener('dragenter', (event) => {
      if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes('Files')) return;
      event.preventDefault();
      dragDepth += 1;
      dropZone.classList.add('dragging');
    });
    dropZone.addEventListener('dragover', (event) => { if (dragDepth > 0) event.preventDefault(); });
    dropZone.addEventListener('dragleave', () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) dropZone.classList.remove('dragging');
    });
    dropZone.addEventListener('drop', (event) => {
      event.preventDefault();
      dragDepth = 0;
      dropZone.classList.remove('dragging');
      uploadFiles(Array.from((event.dataTransfer && event.dataTransfer.files) || []));
    });

    container.appendChild(statusCard);
    container.appendChild(dropZone);

    async function loadStatus() {
      try {
        state.status = await api.get('/api/drive/status');
      } catch (err) {
        state.status = null;
        clear(statusCard).appendChild(el('div', {}, `加载网盘状态失败：${err.message}`));
        return;
      } finally {
        uploadButton.disabled = folderButton.disabled = !state.status?.enabled;
        dropHint.hidden = !state.status?.enabled;
      }
      const status = state.status;
      clear(statusCard);
      if (status.cleanup.pending || status.cleanup.manual) {
        statusCard.appendChild(el('div', {},
          `Telegram 副本：${status.cleanup.pending} 个待清理，${status.cleanup.manual} 个需手动处理（消息编号见机器人日志）。`));
      }
      if (!status.enabled) {
        statusCard.appendChild(el('div', {}, '网盘已关闭，当前只能浏览、复制直链和下载。'));
        return;
      }
      const limitText = status.download_limit_mb
        ? `单文件最大 ${Math.min(status.max_upload_mb, status.download_limit_mb)}MB（Telegram 官方接口限制机器人只能取回 ${status.download_limit_mb}MB 以内的文件）`
        : `单文件最大 ${status.max_upload_mb}MB`;
      statusCard.appendChild(el('div', { class: 'drive-stats' },
        el('span', {}, `📦 ${status.stats.files} 个文件`),
        el('span', {}, `📁 ${status.stats.folders} 个文件夹`),
        el('span', {}, `💾 ${fmtSize(status.stats.bytes)}`),
      ));
      statusCard.appendChild(el('div', { class: 'muted small' },
        `${STORAGE_TEXT[status.storage] || ''}；${limitText}；管理员私聊机器人发图片或文件${status.auto_upload ? '会' : '不会'}自动存进网盘。`));
      if (!status.public_base_configured) {
        statusCard.appendChild(el('div', { class: 'muted small' },
          `直链目前使用 ${status.public_base || '当前访问地址'}，建议在配置中心填写 DRIVE_PUBLIC_URL（例如 https://pm.pivkeyu.de5.net）。`));
      }
    }

    function renderCrumbs(trail) {
      clear(crumbs);
      const nodes = [{ id: null, name: '全部文件' }, ...trail];
      nodes.forEach((node, index) => {
        if (index > 0) crumbs.appendChild(el('span', { class: 'muted' }, ' / '));
        if (index === nodes.length - 1 && !state.query) crumbs.appendChild(el('strong', {}, node.name));
        else crumbs.appendChild(el('a', { href: folderHash(node.id) }, node.name));
      });
      if (state.query) crumbs.appendChild(el('span', { class: 'muted' }, ` · 搜索「${state.query}」`));
    }

    async function loadList() {
      const current = ++generation;
      const query = new URLSearchParams({ sort: state.sort, page: String(state.page) });
      if (parentId) query.set('parent_id', String(parentId));
      if (state.query) query.set('q', state.query);
      let data;
      try {
        data = await api.get(`/api/drive/files?${query}`);
      } catch (err) {
        if (current !== generation || disposed) return;
        clear(listBox).appendChild(el('div', { class: 'muted' }, `加载失败：${err.message}`));
        clear(pagerBox);
        if (err.status === 404) {
          listBox.appendChild(el('a', { href: '#/drive' }, '回到全部文件'));
        }
        return;
      }
      if (current !== generation || disposed) return;
      if (state.page > data.total_pages) {
        state.page = Math.max(1, data.total_pages);
        return loadList();
      }
      renderCrumbs(data.breadcrumbs || []);
      clear(listBox);
      clear(pagerBox);
      if (!data.items.length) {
        listBox.appendChild(el('div', { class: 'muted small drive-empty' },
          state.query ? '没有找到匹配的文件' : '这个文件夹里还没有文件'));
        return;
      }
      for (const item of data.items) listBox.appendChild(itemRow(item));
      if (data.total_pages > 1) {
        pagerBox.appendChild(pager({
          page: data.page,
          totalPages: data.total_pages,
          total: data.total,
          onChange: (page) => { state.page = page; loadList(); },
        }));
      }
    }

    async function refresh() {
      await loadStatus();
      if (!disposed) await loadList();
    }

    function itemRow(item) {
      const open = () => {
        if (item.is_folder) location.hash = folderHash(item.id);
        else showDetail(item);
      };
      const meta = item.is_folder
        ? `文件夹 · ${fmtTime(item.created_at)}`
        : `${fmtSize(item.size)} · ${fmtTime(item.created_at)}`;
      const actions = item.is_folder
        ? editButtons(item)
        : [
          el('button', { class: 'btn sm primary', onClick: () => copyText(item.links.url) }, '复制直链'),
          el('button', { class: 'btn sm', onClick: () => showDetail(item) }, '详情'),
        ];
      return el('div', { class: 'drive-item' },
        el('button', { class: 'drive-item-main', onClick: open, title: item.name },
          thumbNode(item),
          el('span', { class: 'drive-item-text' },
            el('span', { class: 'drive-item-name' }, item.name),
            el('span', { class: 'muted small' }, meta),
          ),
        ),
        el('div', { class: 'drive-item-actions' }, actions),
      );
    }

    function editButtons(item, close = () => {}) {
      if (!state.status?.enabled) return [];
      return [
        el('button', { class: 'btn sm', onClick: () => { close(); renameItem(item); } }, '改名'),
        el('button', { class: 'btn sm', onClick: () => { close(); moveItem(item); } }, '移动'),
        el('button', { class: 'btn sm danger', onClick: () => { close(); deleteItem(item); } }, '删除'),
      ];
    }

    function showDetail(item) {
      const linkRows = LINK_FORMATS.map((format) => el('div', { class: 'drive-link-row' },
        el('span', { class: 'drive-link-label small muted' }, format.label),
        el('input', { class: 'input mono', readonly: true, value: item.links[format.key] }),
        el('button', { class: 'btn sm', onClick: () => copyText(item.links[format.key]) }, '复制'),
      ));
      const dialog = modal({
        title: item.name,
        wide: true,
        body: el('div', { class: 'drive-detail' },
          previewNode(item),
          el('div', { class: 'muted small' },
            `${fmtSize(item.size)} · ${item.mime_type || '未知类型'} · ${fmtTime(item.created_at)}`),
          linkRows,
        ),
        footer: [
          el('a', { class: 'btn', href: item.url, target: '_blank', rel: 'noopener' }, '打开'),
          el('a', { class: 'btn', href: item.download_url, target: '_blank', rel: 'noopener' }, '下载'),
          ...editButtons(item, () => dialog.close()),
        ],
      });
    }

    async function createFolder() {
      const name = await askName({ title: '新建文件夹', placeholder: '文件夹名称', confirmText: '创建' });
      if (!name) return;
      try {
        await api.post('/api/drive/folders', { name, parent_id: parentId });
        toast('文件夹建好啦', 'ok');
        refresh();
      } catch (err) {
        toast(err.message, 'error');
      }
    }

    async function renameItem(item) {
      const name = await askName({ title: '改名', value: item.name });
      if (!name || name === item.name) return;
      try {
        await api.put(`/api/drive/files/${item.id}`, { name });
        toast('已改名', 'ok');
        loadList();
      } catch (err) {
        toast(err.message, 'error');
      }
    }

    async function moveItem(item) {
      let folders;
      try {
        folders = (await api.get('/api/drive/folders')).items;
      } catch (err) {
        toast(err.message, 'error');
        return;
      }
      const select = el('select', { class: 'input' }, el('option', { value: '' }, '/（全部文件）'),
        folderPaths(folders, item.id)
          .map((folder) => el('option', { value: String(folder.id) }, folder.path)));
      select.value = item.parent_id ? String(item.parent_id) : '';
      const confirmButton = el('button', { class: 'btn primary' }, '移动');
      const dialog = modal({
        title: `移动「${item.name}」`,
        body: el('div', { class: 'field' }, el('label', {}, '移动到'), select),
        footer: [el('button', { class: 'btn', onClick: () => dialog.close() }, '取消'), confirmButton],
      });
      confirmButton.addEventListener('click', () => withLoading(confirmButton, async () => {
        try {
          await api.put(`/api/drive/files/${item.id}`, { parent_id: select.value ? Number(select.value) : null });
          dialog.close();
          toast('已移动', 'ok');
          loadList();
        } catch (err) {
          toast(err.message, 'error');
        }
      }));
    }

    async function deleteItem(item) {
      const message = item.is_folder
        ? `删除文件夹「${item.name}」会连同里面的全部文件一起删除，直链也会失效。`
        : `删除「${item.name}」后，它的直链会立即失效。`;
      if (!await confirmDialog({ title: '确认删除', message, confirmText: '删除' })) return;
      try {
        const result = await api.del(`/api/drive/files/${item.id}`);
        const details = [];
        if (result.pending) details.push(`${result.pending} 个 Telegram 副本待后台清理`);
        if (result.manual) details.push(`${result.manual} 个 Telegram 副本需手动删除，消息编号见机器人日志`);
        toast([`已从网盘移除 ${result.deleted} 项`, ...details].join('；'), details.length ? 'info' : 'ok');
        refresh();
      } catch (err) {
        toast(err.message, 'error');
      }
    }

    async function uploadFiles(files) {
      if (!files.length) return;
      const status = state.status;
      if (!status?.enabled) {
        toast('当前不能上传文件', 'error');
        return;
      }
      const limitMb = status
        ? (status.download_limit_mb ? Math.min(status.max_upload_mb, status.download_limit_mb) : status.max_upload_mb)
        : 0;
      const jobs = files.map((file) => {
        const bar = el('div', { class: 'drive-progress-bar' });
        const label = el('span', { class: 'muted small' }, '等待上传');
        const row = el('div', { class: 'drive-queue-item' },
          el('div', { class: 'drive-queue-head' }, el('span', { class: 'drive-item-name' }, file.name), label),
          el('div', { class: 'drive-progress' }, bar),
        );
        queueBox.appendChild(row);
        return { file, bar, label, row };
      });
      let uploaded = 0;
      for (const job of jobs) {
        if (disposed) return;
        if (limitMb && job.file.size > limitMb * 1024 * 1024) {
          job.label.textContent = `超过 ${limitMb}MB，已跳过`;
          job.row.classList.add('failed');
          continue;
        }
        job.label.textContent = '上传中 0%';
        try {
          const query = parentId ? `?parent_id=${parentId}` : '';
          await upload(`/api/drive/upload${query}`, job.file, (ratio) => {
            job.bar.style.width = `${Math.round(ratio * 100)}%`;
            job.label.textContent = ratio >= 1 ? '正在存入 Telegram……' : `上传中 ${Math.round(ratio * 100)}%`;
          });
          job.bar.style.width = '100%';
          job.label.textContent = '完成';
          job.row.classList.add('done');
          uploaded += 1;
          setTimeout(() => job.row.remove(), 2500);
        } catch (err) {
          job.label.textContent = err.message;
          job.row.classList.add('failed');
        }
      }
      if (uploaded && !disposed) {
        toast(`上传完成 ${uploaded} 个文件，点「复制直链」就能拿到链接`, 'ok');
        refresh();
      }
    }

    refresh();
    return () => { disposed = true; };
  },
};
