/* 配置中心：环境变量类配置的分组展示、保存（热生效）与重置。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, switchBox, selectInput } from '../ui.js';

const SOURCE_LABEL = {
  override: ['面板覆盖', 'accent'],
  env: ['.env', 'ok'],
  default: ['默认值', ''],
};

const PASSWORD_CONFIG_KEYS = new Set(['WEB_PANEL_PASSWORD', 'WEB_PANEL_PASSWORD_SHA256']);

function passwordCard() {
  const currentPassword = el('input', {
    class: 'input', type: 'password', autocomplete: 'current-password', placeholder: '当前面板密码',
  });
  const newPassword = el('input', {
    class: 'input', type: 'password', autocomplete: 'new-password', placeholder: '新密码（至少 8 位）',
    maxlength: '128',
  });
  const confirmPassword = el('input', {
    class: 'input', type: 'password', autocomplete: 'new-password', placeholder: '再次输入新密码',
    maxlength: '128',
  });
  const saveButton = el('button', { class: 'btn primary' }, '更改面板密码');
  saveButton.addEventListener('click', async () => {
    if (!currentPassword.value) {
      toast('请输入当前密码', 'error');
      return;
    }
    if (newPassword.value.length < 8 || newPassword.value.length > 128) {
      toast('新密码长度需为 8 到 128 位', 'error');
      return;
    }
    if (newPassword.value !== confirmPassword.value) {
      toast('两次输入的新密码不一致', 'error');
      return;
    }

    saveButton.disabled = true;
    try {
      await api.post('/api/password', {
        current_password: currentPassword.value,
        new_password: newPassword.value,
      });
      currentPassword.value = '';
      newPassword.value = '';
      confirmPassword.value = '';
      toast('面板密码已更改', 'ok');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      saveButton.disabled = false;
    }
  });

  return el('div', { class: 'card' },
    el('h3', {}, '修改面板密码'),
    el('div', { class: 'muted small' }, '登录后可随时更改，不受首次设置的 15 分钟窗口限制。密码以摘要形式保存。'),
    el('div', { class: 'field' }, el('label', {}, '当前密码'), currentPassword),
    el('div', { class: 'field' }, el('label', {}, '新密码'), newPassword),
    el('div', { class: 'field' }, el('label', {}, '确认新密码'), confirmPassword),
    saveButton,
  );
}

function buildControl(item, onSave) {
  const type = item.type;
  if (type === 'bool') {
    const control = switchBox({
      checked: item.value === 'true',
      onChange: (checked) => onSave(checked ? 'true' : 'false'),
    });
    return control;
  }
  if (type === 'enum') {
    const select = selectInput({
      options: item.options.map((option) => ({ value: option, label: option })),
      value: item.value,
      onChange: (value) => onSave(value),
    });
    return el('div', { style: { maxWidth: '260px' } }, select);
  }

  const isSecret = item.secret;
  const input = el('input', {
    class: 'input',
    type: isSecret ? 'password' : (type === 'int' ? 'number' : 'text'),
    value: isSecret ? '' : item.value,
    placeholder: isSecret
      ? (item.has_value ? `已设置（${item.value}），留空不改动` : '未设置')
      : (item.default || ''),
    autocomplete: 'off',
  });

  const save = el('button', {
    class: 'btn sm primary',
    onClick: async () => {
      if (isSecret && !input.value) {
        toast('密钥类配置留空表示不修改；如需清除请点「清除」', 'error');
        return;
      }
      await onSave(input.value);
      if (isSecret) input.value = '';
    },
  }, '保存');

  const buttons = [save];
  if (isSecret && item.has_value) {
    buttons.push(el('button', {
      class: 'btn sm danger',
      onClick: async () => {
        await onSave('');
        input.value = '';
      },
    }, '清除'));
  }
  return el('div', { class: 'controls' }, el('div', { style: { flex: '1', minWidth: '240px' } }, input), buttons);
}

function buildItem(item, onChange) {
  const [sourceText, sourceKind] = SOURCE_LABEL[item.source] || ['未知', ''];
  const top = el('div', { class: 'top' },
    el('span', { class: 'label' }, item.label),
    el('span', { class: 'mono muted small' }, item.key),
    badge(sourceText, sourceKind),
    item.restart ? badge('需重启', 'warn') : null,
    item.secret ? badge('敏感', '') : null,
  );

  async function save(rawValue) {
    try {
      const updated = await api.put(`/api/config/${encodeURIComponent(item.key)}`, { value: rawValue });
      toast(`已保存 ${item.label}${item.restart ? '（重启后生效）' : ''}`, 'ok');
      onChange(updated);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  const controls = buildControl(item, save);
  if (item.source === 'override') {
    controls.appendChild(el('button', {
      class: 'btn sm',
      title: `还原为 ${item.original_value !== undefined ? `「${item.original_value || '空'}」` : '.env 值'}`,
      onClick: async () => {
        try {
          const updated = await api.del(`/api/config/${encodeURIComponent(item.key)}`);
          toast(`已还原 ${item.label}`, 'ok');
          onChange(updated);
        } catch (err) {
          toast(err.message, 'error');
        }
      },
    }, '还原'));
  }

  const node = el('div', { class: 'config-item', dataset: { key: item.key } },
    top,
    el('div', { class: 'hint' }, item.hint),
    controls,
  );
  node._update = (next) => {
    node.replaceWith(buildItem(next, onChange));
  };
  return node;
}

export default {
  title: '配置中心',
  hint: '改完立即生效；标「需重启」的项在进程重启后生效',
  render(container) {
    container.appendChild(spinner());

    async function load() {
      let data;
      try {
        data = await api.get('/api/config');
      } catch (err) {
        clear(container);
        container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
        return;
      }
      clear(container);
      container.appendChild(passwordCard());

      const byCategory = new Map();
      for (const item of data.items) {
        if (PASSWORD_CONFIG_KEYS.has(item.key)) continue;
        if (!byCategory.has(item.category)) byCategory.set(item.category, []);
        byCategory.get(item.category).push(item);
      }

      for (const category of data.categories) {
        const items = byCategory.get(category.key) || [];
        if (items.length === 0) continue;
        const list = el('div', {});
        for (const item of items) {
          list.appendChild(buildItem(item, (updated) => {
            const target = list.querySelector(`[data-key="${CSS.escape(updated.key)}"]`);
            if (target && target._update) target._update(updated);
          }));
        }
        container.appendChild(el('div', { class: 'card' },
          el('h3', {}, category.label, el('span', { class: 'sub' }, `共 ${items.length} 项`)),
          list,
        ));
      }
    }

    load();
  },
};
