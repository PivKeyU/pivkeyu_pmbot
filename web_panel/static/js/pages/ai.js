/* AI 设置：选择提供商协议、填写连接信息、测试连通性、按用途分模型。
 *
 * 协议列表、Base URL 预设、默认模型全部来自后端 services/ai_service.py 的 PROVIDERS，
 * 这里不写死任何提供商。密钥与地址仍然存进配置中心（/api/config/{key}），两边看到的是同一份。
 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge } from '../ui.js';

const FEATURE_LABELS = {
  filter: ['内容审查模型', '判断私聊消息是否为垃圾 / 违规内容'],
  verification: ['验证出题模型', '给新用户出人机验证题'],
  autoreply: ['自动回复模型', '根据知识库回答客人，也用于连接测试'],
};

const CUSTOM_PRESET = '__custom__';

export default {
  title: 'AI 设置',
  hint: '提供商、密钥与模型改完立即生效（无需重启）',
  render(container) {
    let activeTab = null;
    // 保存后页面会重新加载，记住最近一次测试结果，避免提示一闪而过
    let lastTest = null;
    container.appendChild(spinner());

    async function load() {
      let data;
      try {
        data = await api.get('/api/ai-settings');
      } catch (err) {
        clear(container);
        container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
        return;
      }
      clear(container);
      if (!activeTab || !data.providers.some((p) => p.id === activeTab)) activeTab = data.provider;

      container.appendChild(renderProviderPicker(data));
      container.appendChild(renderProviderEditor(data));
    }

    /* ---------------- 当前提供商 ---------------- */

    function renderProviderPicker(data) {
      const grid = el('div', { class: 'provider-grid' });
      for (const provider of data.providers) {
        const isCurrent = provider.id === data.provider;
        const useButton = el('button', {
          class: `btn sm ${isCurrent ? '' : 'primary'}`.trim(),
          disabled: isCurrent,
          onClick: async (event) => {
            event.stopPropagation();
            if (!provider.configured && !window.confirm(`${provider.label} 还没有配置 API Key，确定要切换过去吗？\n切换后 AI 审查 / 出题 / 自动回复都会暂停，直到填好 Key。`)) return;
            useButton.disabled = true;
            try {
              await api.put('/api/ai-settings', { provider: provider.id });
              toast(`已切换到 ${provider.label}`, 'ok');
              activeTab = provider.id;
              await load();
            } catch (err) {
              toast(err.message, 'error');
              useButton.disabled = false;
            }
          },
        }, isCurrent ? '使用中' : '设为当前');

        grid.appendChild(el('div', {
          class: `provider-tile ${isCurrent ? 'current' : ''} ${provider.id === activeTab ? 'editing' : ''}`.trim(),
          onClick: () => { activeTab = provider.id; load(); },
        },
          el('div', { class: 'tile-head' },
            el('span', { class: 'tile-name' }, provider.label),
            provider.configured ? badge('已配置 Key', 'ok') : badge('未配置 Key', 'warn'),
          ),
          el('div', { class: 'tile-protocol' }, provider.protocol),
          el('div', { class: 'tile-foot' },
            el('span', { class: 'muted small' }, provider.base_url ? shortUrl(provider.base_url) : '官方地址'),
            useButton,
          ),
        ));
      }

      return el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '当前提供商', el('span', { class: 'sub' }, '点卡片编辑对应协议的连接与模型')),
        ),
        grid,
      );
    }

    /* ---------------- 连接与模型 ---------------- */

    function renderProviderEditor(data) {
      const provider = data.providers.find((p) => p.id === activeTab);

      const tabs = el('div', { class: 'tabs' }, data.providers.map((p) => el('button', {
        class: p.id === activeTab ? 'active' : '',
        onClick: () => { activeTab = p.id; load(); },
      }, p.label)));

      return el('div', { class: 'card' },
        tabs,
        el('div', { class: 'muted small', style: { marginBottom: '14px' } }, provider.protocol),
        renderConnection(provider, data),
        renderModels(provider, data),
      );
    }

    function renderConnection(provider, data) {
      const keyInput = el('input', {
        class: 'input',
        type: 'password',
        autocomplete: 'off',
        placeholder: provider.key.has_value ? `已保存：${provider.key.value}（留空则不修改）` : '粘贴 API Key',
      });

      const baseInput = el('input', {
        class: 'input',
        value: provider.base_url,
        placeholder: provider.presets[0] && provider.presets[0].url ? provider.presets[0].url : '留空使用官方地址',
      });

      const presetSelect = el('select', { class: 'input' });
      for (const preset of provider.presets) {
        presetSelect.appendChild(el('option', { value: preset.url }, preset.url ? preset.label : `${preset.label}（默认地址）`));
      }
      presetSelect.appendChild(el('option', { value: CUSTOM_PRESET }, '自定义地址 / 反代'));
      const syncPreset = () => {
        const match = provider.presets.find((p) => p.url === baseInput.value.trim());
        presetSelect.value = match ? match.url : CUSTOM_PRESET;
      };
      syncPreset();
      presetSelect.addEventListener('change', () => {
        if (presetSelect.value !== CUSTOM_PRESET) baseInput.value = presetSelect.value;
        baseInput.focus();
      });
      baseInput.addEventListener('input', syncPreset);

      const testResult = el('div', { class: 'test-result muted small' });
      const showTest = (cls, text) => {
        testResult.className = `test-result ${cls} small`;
        testResult.textContent = text;
        lastTest = { provider: provider.id, cls, text };
      };
      if (lastTest && lastTest.provider === provider.id) showTest(lastTest.cls, lastTest.text);
      const saveButton = el('button', { class: 'btn primary' }, '保存连接');
      const testButton = el('button', { class: 'btn' }, '测试连接');

      const isDirty = () => keyInput.value.trim() !== '' || baseInput.value.trim() !== provider.base_url;

      async function saveConnection({ quiet = false } = {}) {
        const key = keyInput.value.trim();
        const base = baseInput.value.trim();
        if (key) await api.put(`/api/config/${provider.key_env}`, { value: key });
        if (base !== provider.base_url) await api.put(`/api/config/${provider.base_env}`, { value: base });
        if (!quiet) toast(`${provider.label} 连接已保存`, 'ok');
      }

      saveButton.addEventListener('click', async () => {
        if (!isDirty()) { toast('没有需要保存的改动'); return; }
        saveButton.disabled = true;
        try {
          await saveConnection();
          await load();
        } catch (err) {
          toast(err.message, 'error');
          saveButton.disabled = false;
        }
      });

      testButton.addEventListener('click', async () => {
        testButton.disabled = true;
        testButton.textContent = '测试中…';
        showTest('muted', '正在发送一条测试消息……');
        try {
          // 有未保存的改动时先保存，测的就是填写中的这一份
          const dirty = isDirty();
          if (dirty) await saveConnection({ quiet: true });
          const model = (data.models[provider.model_keys.autoreply] || '').trim() || undefined;
          const result = await api.post('/api/ai-test', { provider: provider.id, model });
          showTest('ok', `连接成功 · 模型 ${result.model} · 耗时 ${result.latency_ms} ms · 回复「${result.reply || '（空）'}」`);
          if (dirty) {
            toast('连接已保存并测试通过', 'ok');
            setTimeout(load, 1200);
          }
        } catch (err) {
          showTest('error', err.message);
        } finally {
          testButton.disabled = false;
          testButton.textContent = '测试连接';
        }
      });

      return el('div', { class: 'ai-section' },
        el('h4', {}, '连接设置'),
        el('div', { class: 'ai-grid' },
          el('div', { class: 'field' },
            el('label', {}, `API Key（${provider.key_env}）`),
            keyInput,
          ),
          el('div', { class: 'field' },
            el('label', {}, '服务预设'),
            presetSelect,
          ),
          el('div', { class: 'field span-2' },
            el('label', {}, `Base URL（${provider.base_env}）`),
            baseInput,
          ),
        ),
        el('div', { class: 'row', style: { alignItems: 'center' } }, saveButton, testButton),
        testResult,
      );
    }

    function renderModels(provider, data) {
      const inputs = {};
      const lists = {};
      const fields = [];
      for (const [feature, key] of Object.entries(provider.model_keys)) {
        const [label, hint] = FEATURE_LABELS[feature] || [feature, ''];
        const listId = `model-list-${key}`;
        const list = el('datalist', { id: listId });
        lists[key] = list;
        const input = el('input', {
          class: 'input',
          value: data.models[key] || '',
          list: listId,
          placeholder: `默认 ${provider.default_models[feature]}`,
        });
        inputs[key] = input;
        fields.push(el('div', { class: 'field' },
          el('label', {}, label),
          input,
          list,
          el('div', { class: 'muted small', style: { marginTop: '3px' } }, hint),
        ));
      }

      const listStatus = el('span', { class: 'muted small' },
        provider.configured ? '可获取模型列表后从下拉中选择，也可以直接填写模型 ID' : '先填好并保存 API Key 才能获取模型列表');
      const fetchButton = el('button', {
        class: 'btn sm',
        disabled: !provider.configured,
        onClick: async () => {
          fetchButton.disabled = true;
          fetchButton.textContent = '获取中…';
          try {
            const result = await api.get(`/api/ai-models?provider=${provider.id}`);
            for (const list of Object.values(lists)) {
              clear(list);
              for (const model of result.models) list.appendChild(el('option', { value: model }));
            }
            listStatus.textContent = result.models.length
              ? `已获取 ${result.models.length} 个模型，点输入框即可选择`
              : '没有获取到模型，请检查 API Key 与 Base URL（部分服务不提供模型列表，可直接填写）';
          } catch (err) {
            listStatus.textContent = err.message;
            toast(err.message, 'error');
          } finally {
            fetchButton.disabled = !provider.configured;
            fetchButton.textContent = '获取模型列表';
          }
        },
      }, '获取模型列表');

      const saveButton = el('button', { class: 'btn primary' }, '保存模型');
      saveButton.addEventListener('click', async () => {
        const models = {};
        for (const [key, input] of Object.entries(inputs)) {
          const value = input.value.trim();
          if (value) models[key] = value;
        }
        saveButton.disabled = true;
        try {
          await api.put('/api/ai-settings', { models });
          toast(`${provider.label} 模型已保存`, 'ok');
          await load();
        } catch (err) {
          toast(err.message, 'error');
          saveButton.disabled = false;
        }
      });

      return el('div', { class: 'ai-section' },
        el('div', { class: 'card-head', style: { marginBottom: '6px' } },
          el('h4', {}, '按用途分模型'),
          fetchButton,
        ),
        el('div', { style: { marginBottom: '10px' } }, listStatus),
        el('div', { class: 'ai-grid three' }, fields),
        el('div', {}, saveButton),
      );
    }

    load();
  },
};

function shortUrl(url) {
  try {
    return new URL(url).host;
  } catch (err) {
    return url;
  }
}
