/* UI 工具：DOM 构建、提示、弹窗、表格、分页与格式化。 */

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      // onClick -> click：事件类型必须全小写，否则监听器永远不触发
      node.addEventListener(key.slice(2).toLowerCase(), value);
    }
    else node.setAttribute(key, value === true ? '' : value);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else if (child instanceof Node) node.appendChild(child);
    else node.appendChild(document.createTextNode(String(child)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function spinner(label = '加载中……') {
  return el('div', { class: 'muted small', style: { padding: '10px 0' } }, label);
}

export function badge(text, kind = '') {
  return el('span', { class: `badge ${kind}`.trim() }, text);
}

/* ---------- 提示与弹窗 ---------- */

let toastWrap = null;

export function toast(message, type = 'info', timeout = 3600) {
  if (!toastWrap) {
    toastWrap = el('div', { class: 'toast-wrap' });
    document.body.appendChild(toastWrap);
  }
  const node = el('div', { class: `toast ${type}` }, message);
  toastWrap.appendChild(node);
  setTimeout(() => node.remove(), timeout);
}

export function confirmDialog({ title = '确认操作', message = '', confirmText = '确认', danger = true } = {}) {
  return new Promise((resolve) => {
    const close = (result) => {
      mask.remove();
      resolve(result);
    };
    const mask = el('div', { class: 'modal-mask' },
      el('div', { class: 'modal' },
        el('header', {}, title),
        el('div', { class: 'body' }, el('div', {}, message)),
        el('footer', {},
          el('button', { class: 'btn', onClick: () => close(false) }, '取消'),
          el('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onClick: () => close(true) }, confirmText),
        ),
      ),
    );
    mask.addEventListener('click', (event) => {
      if (event.target === mask) close(false);
    });
    document.body.appendChild(mask);
  });
}

export function modal({ title, body, footer = [], wide = false }) {
  const mask = el('div', { class: 'modal-mask' });
  const close = () => mask.remove();
  const box = el('div', { class: `modal ${wide ? 'wide' : ''}`.trim() },
    el('header', {}, title),
    el('div', { class: 'body' }, body),
    el('footer', {}, footer),
  );
  mask.appendChild(box);
  mask.addEventListener('click', (event) => {
    if (event.target === mask) close();
  });
  document.body.appendChild(mask);
  return { close, mask };
}

/* ---------- 表格与分页 ---------- */

export function table({ columns, rows, empty = '暂无数据', rowKey }) {
  if (!rows || rows.length === 0) {
    return el('div', { class: 'muted small', style: { padding: '8px 0' } }, empty);
  }
  const thead = el('thead', {}, el('tr', {}, columns.map((col) => el('th', { class: col.className || '' }, col.title))));
  const tbody = el('tbody', {}, rows.map((row, index) => el('tr', { dataset: rowKey ? { key: rowKey(row) } : {} },
    columns.map((col) => el('td', { class: col.className || '' }, col.render ? col.render(row, index) : row[col.key])),
  )));
  return el('div', { class: 'table-wrap' }, el('table', { class: 'table' }, thead, tbody));
}

export function pager({ page, totalPages, total, onChange }) {
  return el('div', { class: 'pager' },
    el('span', {}, `共 ${total} 条 · 第 ${page}/${totalPages} 页`),
    el('button', { class: 'btn sm', disabled: page <= 1, onClick: () => onChange(page - 1) }, '上一页'),
    el('button', { class: 'btn sm', disabled: page >= totalPages, onClick: () => onChange(page + 1) }, '下一页'),
  );
}

/* ---------- 表单控件 ---------- */

export function field(label, control, hint) {
  return el('div', { class: 'field' },
    label ? el('label', {}, label) : null,
    control,
    hint ? el('div', { class: 'hint muted small' }, hint) : null,
  );
}

export function textInput({ value = '', placeholder = '', type = 'text', onEnter } = {}) {
  const input = el('input', { class: 'input', type, value, placeholder });
  if (onEnter) {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        onEnter(input.value);
      }
    });
  }
  return input;
}

export function switchBox({ checked = false, disabled = false, onChange } = {}) {
  const input = el('input', { type: 'checkbox', checked, disabled });
  if (onChange) input.addEventListener('change', () => onChange(input.checked));
  return el('label', { class: 'switch' }, input, el('span', { class: 'slider' }));
}

export function selectInput({ options = [], value = '', onChange } = {}) {
  const select = el('select', { class: 'input' });
  for (const option of options) {
    const opt = el('option', { value: option.value }, option.label);
    if (String(option.value) === String(value)) opt.selected = true;
    select.appendChild(opt);
  }
  if (onChange) select.addEventListener('change', () => onChange(select.value));
  return select;
}

export function chips(items, onRemove) {
  return el('div', {}, items.map((item) => el('span', { class: 'chip' }, String(item),
    onRemove ? el('button', { title: '移除', onClick: () => onRemove(item) }, '×') : null,
  )));
}

/* ---------- 格式化 ---------- */

export function fmtTime(value) {
  if (value === null || value === undefined || value === '') return '-';
  let date;
  if (typeof value === 'number') {
    date = new Date(value * 1000);
  } else {
    const text = String(value);
    date = new Date(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(text) ? `${text.replace(' ', 'T')}Z` : text);
  }
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fmtRelative(value) {
  if (!value) return '';
  let date;
  if (typeof value === 'number') date = new Date(value * 1000);
  else {
    const text = String(value);
    date = new Date(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(text) ? `${text.replace(' ', 'T')}Z` : text);
  }
  if (Number.isNaN(date.getTime())) return '';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return fmtTime(value).slice(0, 10);
}

export function userLabel(row) {
  if (!row) return '未知';
  const name = row.first_name || row.username || `用户 ${row.user_id}`;
  return name;
}

export function withLoading(button, task) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '处理中……';
  return Promise.resolve()
    .then(task)
    .finally(() => {
      button.disabled = false;
      button.textContent = original;
    });
}
