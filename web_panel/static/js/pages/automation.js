/* 自动回复（总开关 + 知识库 CRUD）与垃圾关键词（开关 + 词表）。 */

import { api } from '../api.js';
import { el, clear, spinner, toast, badge, confirmDialog, table, switchBox, chips } from '../ui.js';

function knowledgeDialog(entry, onDone) {
  const titleInput = el('input', { class: 'input', value: entry ? entry.title : '', placeholder: '例如：价格表' });
  const contentInput = el('textarea', { class: 'input', rows: '6', placeholder: '知识内容，AI 会据此回答用户' }, entry ? entry.content : '');
  const mask = el('div', { class: 'modal-mask' });
  const close = () => mask.remove();
  mask.appendChild(el('div', { class: 'modal wide' },
    el('header', {}, entry ? '编辑知识条目' : '新增知识条目'),
    el('div', { class: 'body' },
      el('div', { class: 'field' }, el('label', {}, '标题'), titleInput),
      el('div', { class: 'field' }, el('label', {}, '内容'), contentInput),
    ),
    el('footer', {},
      el('button', { class: 'btn', onClick: close }, '取消'),
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          const title = titleInput.value.trim();
          const content = contentInput.value.trim();
          if (!title || !content) {
            toast('标题与内容都不能为空', 'error');
            return;
          }
          try {
            if (entry) await api.put(`/api/knowledge/${entry.id}`, { title, content });
            else await api.post('/api/knowledge', { title, content });
            toast('已保存', 'ok');
            close();
            onDone();
          } catch (err) {
            toast(err.message, 'error');
          }
        },
      }, '保存'),
    ),
  ));
  mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
  titleInput.focus();
}

export default {
  title: '回复与关键词',
  render(container) {
    const replyCard = el('div', { class: 'card' });
    const kbCard = el('div', { class: 'card' });
    const spamCard = el('div', { class: 'card' });
    container.appendChild(replyCard);
    container.appendChild(kbCard);
    container.appendChild(spamCard);

    /* ---------------- 自动回复 ---------------- */

    async function loadAutoreply() {
      clear(replyCard);
      replyCard.appendChild(spinner());
      let data;
      try {
        data = await api.get('/api/autoreply');
      } catch (err) {
        clear(replyCard);
        replyCard.appendChild(el('div', {}, err.message));
        return;
      }
      clear(replyCard);
      replyCard.appendChild(el('div', { class: 'card-head' },
        el('h3', {}, 'AI 自动回复'),
        switchBox({
          checked: data.enabled,
          onChange: async (checked) => {
            try {
              await api.put('/api/autoreply', { enabled: checked });
              toast(checked ? '自动回复已开启' : '自动回复已关闭', 'ok');
            } catch (err) {
              toast(err.message, 'error');
              loadAutoreply();
            }
          },
        }),
      ));
      replyCard.appendChild(el('div', { class: 'muted small' }, '开启后，AI 会依据下面的知识库内容自动回复用户私聊。'));

      const personalityInput = el('textarea', {
        class: 'input',
        rows: '14',
        maxlength: '20000',
        placeholder: '# 人格设定\n写下称呼、语气、性格和表达习惯。留空时使用默认女仆人格。',
        style: { fontFamily: 'var(--font-mono, monospace)', lineHeight: '1.6' },
      });
      personalityInput.value = data.personality_md || '';
      const personalityCount = el('span', { class: 'muted small' }, `${personalityInput.value.length} / 20000`);
      personalityInput.addEventListener('input', () => {
        personalityCount.textContent = `${personalityInput.value.length} / 20000`;
      });
      const savePersonality = el('button', { class: 'btn sm primary' }, '保存人格.md');
      savePersonality.addEventListener('click', async () => {
        savePersonality.disabled = true;
        try {
          const updated = await api.put('/api/autoreply', { personality_md: personalityInput.value });
          data.personality_md = updated.personality_md;
          toast('人格.md 已保存', 'ok');
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          savePersonality.disabled = false;
        }
      });
      replyCard.appendChild(el('div', { class: 'field', style: { marginTop: '14px' } },
        el('label', {}, '人格.md（自动回复角色设定）'),
        el('div', { class: 'muted small' }, '支持 Markdown，留空时使用默认人格。人格只调整表达风格，知识库回答和未命中兜底规则保持固定。'),
        personalityInput,
        el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center', marginTop: '8px' } },
          savePersonality,
          personalityCount,
        ),
      ));
    }

    /* ---------------- 知识库 ---------------- */

    async function loadKnowledge() {
      clear(kbCard);
      kbCard.appendChild(spinner());
      let data;
      try {
        data = await api.get('/api/knowledge');
      } catch (err) {
        clear(kbCard);
        kbCard.appendChild(el('div', {}, err.message));
        return;
      }
      clear(kbCard);
      kbCard.appendChild(el('div', { class: 'card-head' },
        el('h3', {}, '知识库', el('span', { class: 'sub' }, `共 ${data.items.length} 条`)),
        el('button', { class: 'btn sm primary', onClick: () => knowledgeDialog(null, loadKnowledge) }, '新增条目'),
      ));
      kbCard.appendChild(table({
        columns: [
          { title: 'ID', render: (row) => el('span', { class: 'mono' }, row.id) },
          { title: '标题', render: (row) => row.title },
          { title: '内容', render: (row) => el('div', { style: { maxWidth: '520px', whiteSpace: 'pre-wrap' } }, (row.content || '').slice(0, 200)) },
          {
            title: '',
            className: 'actions',
            render: (row) => el('div', { style: { display: 'flex', gap: '6px', justifyContent: 'flex-end' } },
              el('button', { class: 'btn sm', onClick: () => knowledgeDialog(row, loadKnowledge) }, '编辑'),
              el('button', {
                class: 'btn sm danger',
                onClick: async () => {
                  if (!(await confirmDialog({ title: '删除条目', message: `确定删除「${row.title}」吗？`, confirmText: '删除' }))) return;
                  try {
                    await api.del(`/api/knowledge/${row.id}`);
                    toast('已删除', 'ok');
                    loadKnowledge();
                  } catch (err) { toast(err.message, 'error'); }
                },
              }, '删除'),
            ),
          },
        ],
        rows: data.items,
        empty: '知识库还是空的，先添加几条让女仆背下来吧。',
      }));
    }

    /* ---------------- 垃圾关键词 ---------------- */

    async function loadSpam() {
      clear(spamCard);
      spamCard.appendChild(spinner());
      let data;
      try {
        data = await api.get('/api/spam-keywords');
      } catch (err) {
        clear(spamCard);
        spamCard.appendChild(el('div', {}, err.message));
        return;
      }
      clear(spamCard);

      const keywordInput = el('input', { class: 'input', placeholder: '输入要拦截的关键词，回车添加' });
      keywordInput.addEventListener('keydown', async (event) => {
        if (event.key !== 'Enter') return;
        const keyword = keywordInput.value.trim();
        if (!keyword) return;
        try {
          await api.post('/api/spam-keywords', { keyword });
          keywordInput.value = '';
          toast('已添加', 'ok');
          loadSpam();
        } catch (err) {
          toast(err.message, 'error');
        }
      });

      spamCard.appendChild(el('div', { class: 'card-head' },
        el('h3', {}, '关键词广告拦截'),
        el('div', { style: { display: 'flex', gap: '12px', alignItems: 'center' } },
          el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } },
            '启用拦截',
            switchBox({
              checked: data.enabled,
              onChange: async (checked) => {
                try {
                  await api.put('/api/spam-keywords/settings', { enabled: checked });
                  toast('已更新', 'ok');
                } catch (err) { toast(err.message, 'error'); loadSpam(); }
              },
            }),
          ),
          el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } },
            '命中自动拉黑',
            switchBox({
              checked: data.auto_block,
              onChange: async (checked) => {
                try {
                  await api.put('/api/spam-keywords/settings', { auto_block: checked });
                  toast('已更新', 'ok');
                } catch (err) { toast(err.message, 'error'); loadSpam(); }
              },
            }),
          ),
        ),
      ));

      spamCard.appendChild(el('div', { class: 'field' }, keywordInput));
      spamCard.appendChild(chips(data.keywords, async (keyword) => {
        try {
          await api.del(`/api/spam-keywords/${encodeURIComponent(keyword)}`);
          toast('已移除', 'ok');
          loadSpam();
        } catch (err) { toast(err.message, 'error'); }
      }));
      if (data.keywords.length === 0) {
        spamCard.appendChild(el('div', { class: 'muted small' }, '还没有关键词，命中这些词的私聊消息会进拦截篮。'));
      } else {
        spamCard.appendChild(el('div', { style: { marginTop: '8px' } },
          el('button', {
            class: 'btn sm danger',
            onClick: async () => {
              if (!(await confirmDialog({ title: '清空关键词', message: `确定清空全部 ${data.keywords.length} 个关键词吗？`, confirmText: '清空' }))) return;
              try {
                await api.del('/api/spam-keywords');
                toast('已清空', 'ok');
                loadSpam();
              } catch (err) { toast(err.message, 'error'); }
            },
          }, '清空全部'),
        ));
      }
    }

    loadAutoreply();
    loadKnowledge();
    loadSpam();
  },
};
