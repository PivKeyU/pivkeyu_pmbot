/* AI 设置：所有和 AI 有关的配置都在这一页。
 *
 * - 提供商与模型：协议切换、API Key / Base URL、按用途分模型、连接测试
 * - 功能开关：AI 内容审查、自动回复、误杀申诉按钮、AI 自动撤销验证
 * - 生成参数：思考等级、最大输出 token、超时、输入截断、额外请求参数
 * - 提示词：审查规则、出题规则、自动回复人格.md
 *
 * 协议列表、Base URL 预设、默认模型全部来自后端 services/ai_providers.py，这里不写死任何提供商。
 * 密钥与地址存在配置覆盖层（/api/config/{key}），重启后仍然有效。
 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, switchBox } from '../ui.js';

const FEATURE_LABELS = {
  filter: ['内容审查模型', '判断私聊消息是否为垃圾 / 违规内容'],
  verification: ['验证出题模型', '给新用户出人机验证题'],
  autoreply: ['自动回复模型', '根据知识库回答客人，也用于连接测试'],
};

const CUSTOM_PRESET = '__custom__';

const SECTIONS = [
  ['providers', '提供商与模型'],
  ['switches', '功能开关'],
  ['params', '生成参数'],
  ['prompts', '提示词'],
];

const FEATURE_NAMES = { filter: '内容审查', verification: '验证出题', autoreply: '自动回复' };

const EFFORT_LABELS = {
  '': '默认（不发送）',
  off: '关闭思考（最快）',
  low: '低',
  medium: '中',
  high: '高',
};

// 「额外请求参数」的常见写法，点一下就能填进去
const EXTRA_BODY_EXAMPLES = {
  openai: [
    ['DeepSeek / 智谱 / 豆包关闭思考', '{"thinking": {"type": "disabled"}}'],
    ['通义千问关闭思考', '{"enable_thinking": false}'],
    ['降低随机性', '{"temperature": 0.2}'],
  ],
  anthropic: [
    ['降低思考强度', '{"output_config": {"effort": "low"}}'],
  ],
  gemini: [
    ['降低随机性', '{"temperature": 0.2}'],
  ],
};

export default {
  title: 'AI 设置',
  hint: '提供商、密钥与模型改完立即生效（无需重启）',
  render(container) {
    let activeTab = null;
    let activeSection = 'providers';
    try { activeSection = localStorage.getItem('ai-settings-section') || 'providers'; } catch (err) { /* 忽略 */ }
    // 保存后页面会重新加载，记住最近一次测试结果，避免提示一闪而过
    let lastTest = null;
    container.appendChild(spinner());

    async function load() {
      let data;
      let options;
      let autoreply;
      let configItems;
      try {
        [data, options, autoreply, configItems] = await Promise.all([
          api.get('/api/ai-settings'),
          api.get('/api/ai-options'),
          api.get('/api/autoreply'),
          api.get('/api/config'),
        ]);
      } catch (err) {
        clear(container);
        container.appendChild(el('div', { class: 'card' }, `加载失败：${err.message}`));
        return;
      }
      clear(container);
      if (!activeTab || !data.providers.some((p) => p.id === activeTab)) activeTab = data.provider;
      if (!SECTIONS.some(([id]) => id === activeSection)) activeSection = 'providers';

      container.appendChild(el('div', { class: 'tabs section-tabs' }, SECTIONS.map(([id, label]) => el('button', {
        class: id === activeSection ? 'active' : '',
        onClick: () => {
          activeSection = id;
          try { localStorage.setItem('ai-settings-section', id); } catch (err) { /* 忽略 */ }
          load();
        },
      }, label))));

      if (activeSection === 'providers') {
        container.appendChild(renderProviderPicker(data));
        container.appendChild(renderProviderEditor(data));
      } else if (activeSection === 'switches') {
        const configByKey = Object.fromEntries(configItems.items.map((item) => [item.key, item]));
        container.appendChild(renderSwitches(options, autoreply, configByKey));
      } else if (activeSection === 'params') {
        container.appendChild(renderParams(options, data));
      } else {
        container.appendChild(renderPrompts(options, autoreply));
      }
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

      const testFeature = el('select', { class: 'input', style: { width: 'auto' }, title: '按这个功能的模型、思考等级、token 与超时来测试' },
        Object.entries(FEATURE_NAMES).map(([id, name]) => el('option', { value: id }, `按「${name}」参数测试`)));
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
          const feature = testFeature.value;
          const model = (data.models[provider.model_keys[feature]] || '').trim() || undefined;
          const result = await api.post('/api/ai-test', { provider: provider.id, model, feature });
          showTest('ok', `连接成功 · ${FEATURE_NAMES[feature]} · 模型 ${result.model} · 耗时 ${result.latency_ms} ms · 回复「${result.reply || '（空）'}」`);
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
        el('div', { class: 'row', style: { alignItems: 'center' } }, saveButton, testButton, testFeature),
        el('div', { class: 'muted small', style: { marginTop: '6px' } },
          '审查慢时先测一下「内容审查」的耗时，再到「生成参数」里把审查的思考等级调成「关闭思考」或「低」。'),
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

/* ---------------- 功能开关 ---------------- */

function settingRow(title, hint, control) {
  return el('div', { class: 'setting-row' },
    el('div', { class: 'setting-text' },
      el('div', { class: 'setting-title' }, title),
      hint ? el('div', { class: 'muted small' }, hint) : null,
    ),
    el('div', { class: 'setting-control' }, control),
  );
}

async function guarded(task, okText) {
  try {
    await task();
    if (okText) toast(okText, 'ok');
    return true;
  } catch (err) {
    toast(err.message, 'error');
    return false;
  }
}

function renderSwitches(options, autoreply, configByKey) {
  const filterItem = configByKey.ENABLE_AI_FILTER;
  const sw = options.switches;

  const thresholdInput = el('input', { class: 'input', type: 'number', min: '1', max: '50', value: String(sw.auto_revoke_threshold), style: { width: '90px' } });
  const windowInput = el('input', { class: 'input', type: 'number', min: '1', max: '720', value: String(sw.auto_revoke_window_hours), style: { width: '90px' } });
  const saveRevoke = el('button', { class: 'btn sm primary' }, '保存');
  saveRevoke.addEventListener('click', () => guarded(() => api.put('/api/ai-options', {
    switches: { auto_revoke_threshold: thresholdInput.value, auto_revoke_window_hours: windowInput.value },
  }), '自动撤销规则已保存'));

  return el('div', { class: 'card' },
    el('h3', {}, '功能开关', el('span', { class: 'sub' }, '改完立即生效')),
    settingRow('AI 内容审查', '对私聊消息做 AI 垃圾 / 违规审查；关闭后消息直接转给管理员（关键词拦截仍然有效）。',
      switchBox({
        checked: filterItem ? filterItem.value === 'true' : true,
        onChange: (checked) => guarded(() => api.put('/api/config/ENABLE_AI_FILTER', { value: checked ? 'true' : 'false' }),
          checked ? 'AI 内容审查已开启' : 'AI 内容审查已关闭'),
      })),
    settingRow('AI 自动回复', '开启后，AI 会依据「回复与关键词」页的知识库自动回复用户。',
      switchBox({
        checked: autoreply.enabled,
        onChange: (checked) => guarded(() => api.put('/api/autoreply', { enabled: checked }),
          checked ? '自动回复已开启' : '自动回复已关闭'),
      })),
    settingRow('误杀申诉按钮', '消息被 AI 拦下时，给用户一个「申请暂停审查」按钮；申请会发到他的 TG 话题和网页会话里，由管理员决定暂停多久。',
      switchBox({
        checked: sw.appeal_enabled,
        onChange: (checked) => guarded(() => api.put('/api/ai-options', { switches: { appeal_enabled: checked } }),
          checked ? '申诉按钮已开启' : '申诉按钮已关闭'),
      })),
    settingRow('AI 自动撤销验证', '已验证用户在一段时间内被 AI 拦下达到次数后，自动收回验证结果，让他重新答题。也可以在会话里手动「撤销验证」，或在话题里发 /unverify。',
      switchBox({
        checked: sw.auto_revoke_enabled,
        onChange: (checked) => guarded(() => api.put('/api/ai-options', { switches: { auto_revoke_enabled: checked } }),
          checked ? 'AI 自动撤销验证已开启' : 'AI 自动撤销验证已关闭'),
      })),
    el('div', { class: 'row', style: { alignItems: 'center', margin: '4px 0 0', paddingLeft: '2px' } },
      el('span', { class: 'small' }, '规则：'), windowInput, el('span', { class: 'small' }, '小时内被拦'),
      thresholdInput, el('span', { class: 'small' }, '次就撤销'), saveRevoke),
  );
}

/* ---------------- 生成参数 ---------------- */

function renderParams(options, data) {
  const rows = {};
  const featureRows = Object.entries(FEATURE_NAMES).map(([feature, name]) => {
    const current = options.features[feature];
    const effort = el('select', { class: 'input' },
      options.effort_levels.map((level) => el('option', { value: level }, EFFORT_LABELS[level] || level)));
    effort.value = current.effort;
    const maxTokens = el('input', { class: 'input', type: 'number', min: '16', placeholder: '默认', value: current.max_tokens });
    const timeout = el('input', { class: 'input', type: 'number', min: '3', max: '600', placeholder: `默认 ${current.default_timeout}`, value: current.timeout });
    rows[feature] = { effort, maxTokens, timeout };
    return el('tr', {},
      el('td', {}, el('strong', {}, name)),
      el('td', {}, effort),
      el('td', {}, maxTokens),
      el('td', {}, timeout),
    );
  });

  const maxInput = el('input', { class: 'input', type: 'number', min: '100', placeholder: `默认 ${options.default_max_input_chars}`, value: options.max_input_chars });
  const maxKb = el('input', { class: 'input', type: 'number', min: '500', placeholder: `默认 ${options.default_max_kb_chars}`, value: options.max_kb_chars });

  const extraInputs = {};
  const extraFields = data.providers.map((provider) => {
    const textarea = el('textarea', { class: 'input mono', rows: '3', placeholder: '留空表示不加；必须是 JSON 对象' });
    textarea.value = options.extra_body[provider.id] || '';
    extraInputs[provider.id] = textarea;
    const examples = (EXTRA_BODY_EXAMPLES[provider.id] || []).map(([label, value]) =>
      el('button', { class: 'btn sm ghost', onClick: () => { textarea.value = value; } }, label));
    return el('div', { class: 'field' },
      el('label', {}, `${provider.label} 额外请求参数`),
      textarea,
      examples.length ? el('div', { class: 'row', style: { marginTop: '4px' } }, el('span', { class: 'muted small' }, '常用：'), examples) : null,
    );
  });

  const saveButton = el('button', { class: 'btn primary' }, '保存生成参数');
  saveButton.addEventListener('click', async () => {
    const features = {};
    for (const [feature, row] of Object.entries(rows)) {
      features[feature] = { effort: row.effort.value, max_tokens: row.maxTokens.value, timeout: row.timeout.value };
    }
    const extra_body = {};
    for (const [provider, textarea] of Object.entries(extraInputs)) extra_body[provider] = textarea.value;
    saveButton.disabled = true;
    await guarded(() => api.put('/api/ai-options', {
      features, max_input_chars: maxInput.value, max_kb_chars: maxKb.value, extra_body,
    }), '生成参数已保存');
    saveButton.disabled = false;
  });

  return el('div', { class: 'card' },
    el('h3', {}, '按功能控制模型', el('span', { class: 'sub' }, '对三种协议都生效，留空 = 用服务商默认')),
    el('div', { class: 'table-wrap' }, el('table', { class: 'table params-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, '功能'), el('th', {}, '思考等级'), el('th', {}, '最大输出 token'), el('th', {}, '超时（秒）'))),
      el('tbody', {}, featureRows),
    )),
    el('div', { class: 'muted small', style: { margin: '8px 0 16px', lineHeight: '1.8' } },
      el('div', {}, '· 思考等级：「关闭思考」最快，适合内容审查；会按服务商自动换成对应参数（OpenAI / Gemini 用 reasoning_effort，通义用 enable_thinking，DeepSeek / 智谱 / 豆包用 thinking，Claude 用 effort，Claude 新模型无法完全关闭，按「低」处理）。不支持的模型会报错，可在「提供商与模型」里测一下。'),
      el('div', {}, '· 最大输出 token：限制模型回复长度；开启思考时思考内容也会算进去，设太小可能导致没有输出。'),
      el('div', {}, '· 超时：审查超时后消息会直接放行（不会卡住用户），出题超时会改用本地题库。'),
    ),
    el('div', { class: 'ai-grid' },
      el('div', { class: 'field' }, el('label', {}, '用户消息最多发送字符数（审查 / 自动回复）'), maxInput),
      el('div', { class: 'field' }, el('label', {}, '知识库最多发送字符数（自动回复）'), maxKb),
    ),
    el('h4', { style: { margin: '10px 0 8px' } }, '额外请求参数（高级）'),
    el('div', { class: 'muted small', style: { marginBottom: '10px' } },
      '会原样合并进请求体，并覆盖上面的思考等级设置，用来处理各家独有的参数。'),
    extraFields,
    saveButton,
  );
}

/* ---------------- 提示词 ---------------- */

function promptEditor({ title, hint, value, defaultValue, fixedSuffix, maxChars, onSave }) {
  const textarea = el('textarea', { class: 'input prompt-input', rows: '10', maxlength: String(maxChars) });
  textarea.value = value || defaultValue || '';
  const counter = el('span', { class: 'muted small' });
  const syncCount = () => { counter.textContent = `${textarea.value.length} / ${maxChars}`; };
  textarea.addEventListener('input', syncCount);
  syncCount();
  const saveButton = el('button', { class: 'btn sm primary' }, '保存');
  saveButton.addEventListener('click', async () => {
    saveButton.disabled = true;
    // 和默认值完全一样时存空，以后默认提示词升级也能跟着生效
    const text = textarea.value.trim() === (defaultValue || '').trim() ? '' : textarea.value;
    await guarded(() => onSave(text), `${title}已保存`);
    saveButton.disabled = false;
  });
  const resetButton = el('button', {
    class: 'btn sm',
    onClick: () => { textarea.value = defaultValue || ''; syncCount(); toast('已填入默认内容，点「保存」后生效'); },
  }, '恢复默认');
  return el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h3', {}, title, value ? badge('已自定义', 'accent') : badge('默认', '')),
    ),
    el('div', { class: 'muted small', style: { marginBottom: '8px' } }, hint),
    textarea,
    fixedSuffix ? el('details', { class: 'fixed-suffix' },
      el('summary', { class: 'small muted' }, '查看自动附加在后面的固定格式要求（不可修改，保证程序能解析结果）'),
      el('pre', { class: 'result' }, fixedSuffix),
    ) : null,
    el('div', { class: 'row', style: { alignItems: 'center', marginTop: '8px' } }, saveButton, resetButton, counter),
  );
}

function renderPrompts(options, autoreply) {
  return el('div', {},
    el('div', { class: 'card muted small' },
      'AI 审查的原理：把用户的文字和图片连同下面的「审查规则」发给模型，模型返回是否违规（is_spam）和理由；判为违规就拦下消息并记入拦截篮。审查出错或超时时消息会放行。'),
    promptEditor({
      title: '内容审查规则',
      hint: '告诉 AI 什么算违规、什么算正常。可以按你的社群调整尺度，例如允许讨论某些话题。',
      value: options.prompts.review,
      defaultValue: options.default_prompts.review,
      fixedSuffix: options.fixed_suffix.review,
      maxChars: 20000,
      onSave: (text) => api.put('/api/ai-options', { prompts: { review: text } }),
    }),
    promptEditor({
      title: '验证出题规则',
      hint: 'AI 出人机验证文字题时遵守的规则。每次出题还会随机附加一个主题和题型，保证题目不重复。',
      value: options.prompts.verification,
      defaultValue: options.default_prompts.verification,
      fixedSuffix: options.fixed_suffix.verification,
      maxChars: 20000,
      onSave: (text) => api.put('/api/ai-options', { prompts: { verification: text } }),
    }),
    promptEditor({
      title: '自动回复人格.md',
      hint: '自动回复的称呼、语气、性格。人格只调整表达风格，「只按知识库回答」和「答不上来时的兜底句」规则固定生效。留空使用默认女仆人格。',
      value: autoreply.personality_md,
      defaultValue: '',
      maxChars: 20000,
      onSave: (text) => api.put('/api/autoreply', { personality_md: text }),
    }),
  );
}

function shortUrl(url) {
  try {
    return new URL(url).host;
  } catch (err) {
    return url;
  }
}
