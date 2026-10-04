/* AI 设置：提供商切换与按用途分模型。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, selectInput } from '../ui.js';

const MODEL_FIELDS = {
  gemini: [
    ['gemini_model_filter', '内容审查模型'],
    ['gemini_model_verification', '验证出题模型'],
    ['gemini_model_autoreply', '自动回复模型'],
  ],
  openai: [
    ['openai_model_filter', '内容审查模型'],
    ['openai_model_verification', '验证出题模型'],
    ['openai_model_autoreply', '自动回复模型'],
  ],
};

export default {
  title: 'AI 设置',
  hint: '提供商与模型改完立即生效（无需重启）',
  render(container) {
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

      const providerSelect = selectInput({
        options: [
          { value: 'gemini', label: 'Gemini' },
          { value: 'openai', label: 'OpenAI 兼容' },
        ],
        value: data.provider,
      });

      const inputs = {};
      const modelsBody = el('div', {});
      for (const provider of ['gemini', 'openai']) {
        const keyConfigured = data.keys[`${provider}_configured`];
        const providerLabel = provider === 'gemini' ? 'Gemini 模型' : 'OpenAI 兼容模型';
        const listStatus = el('span', { class: 'muted small' }, keyConfigured ? '已配置 API Key' : '请先配置 API Key');
        const fetchButton = el('button', {
          class: 'btn sm',
          disabled: !keyConfigured,
          onClick: async () => {
            fetchButton.disabled = true;
            fetchButton.textContent = '获取中…';
            try {
              const result = await api.get(`/api/ai-models?provider=${provider}`);
              for (const [key] of MODEL_FIELDS[provider]) {
                const list = modelLists[key];
                clear(list);
                for (const model of result.models) list.appendChild(el('option', { value: model }));
              }
              listStatus.textContent = result.models.length
                ? `已获取 ${result.models.length} 个模型，输入框可选择或手动填写`
                : '没有获取到模型，请检查 API Key 与 Base URL';
              if (result.models.length) toast(`已获取 ${providerLabel}列表`, 'ok');
            } catch (err) {
              listStatus.textContent = err.message;
              toast(err.message, 'error');
            } finally {
              fetchButton.disabled = !keyConfigured;
              fetchButton.textContent = '获取模型列表';
            }
          },
        }, '获取模型列表');
        modelsBody.appendChild(el('div', {
          style: { marginTop: '16px', display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' },
        },
          el('h3', { style: { margin: '0' } }, providerLabel),
          el('span', { class: 'sub' }, keyConfigured ? '已配置 API Key' : '未配置 API Key'),
          fetchButton,
        ));
        modelsBody.appendChild(el('div', { class: 'muted small', style: { margin: '6px 0 10px' } }, listStatus));

        const modelLists = {};
        for (const [key, label] of MODEL_FIELDS[provider]) {
          const listId = `model-list-${key}`;
          const list = el('datalist', { id: listId });
          modelLists[key] = list;
          const input = el('input', {
            class: 'input',
            value: data.models[key] || '',
            list: listId,
            placeholder: '获取列表后选择，或直接填写模型 ID',
          });
          inputs[key] = input;
          modelsBody.appendChild(el('div', { class: 'field' },
            el('label', {}, `${label}（${key}）`),
            input,
            list,
          ));
        }
      }

      const saveButton = el('button', { class: 'btn primary' }, '保存 AI 设置');
      saveButton.addEventListener('click', async () => {
        saveButton.disabled = true;
        saveButton.textContent = '保存中……';
        try {
          const models = {};
          for (const [key, input] of Object.entries(inputs)) {
            const value = input.value.trim();
            if (value) models[key] = value;
          }
          const updated = await api.put('/api/ai-settings', { provider: providerSelect.value, models });
          toast('AI 设置已更新', 'ok');
          data = updated;
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          saveButton.disabled = false;
          saveButton.textContent = '保存 AI 设置';
        }
      });

      container.appendChild(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, '当前提供商'),
          badge(data.provider === 'gemini' ? 'Gemini' : 'OpenAI 兼容', 'accent'),
        ),
        el('div', { style: { maxWidth: '280px' } }, providerSelect),
        el('div', { class: 'muted small', style: { marginTop: '8px' } },
          'API Key 与 Base URL 请到「配置中心 → AI 能力」里修改，保存后立即重建客户端。'),
      ));

      container.appendChild(el('div', { class: 'card' },
        el('h3', {}, '按用途分模型'),
        modelsBody,
        el('div', { style: { marginTop: '14px' } }, saveButton),
      ));
    }

    load();
  },
};
