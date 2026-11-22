import { escapeHtml as h, safeExternalUrl } from './views.js';
import { todoCategories } from './schedule.js';

const labels = { 'deepseek-chat': 'DeepSeek', 'deepseek-reasoner': 'DeepSeek · 深度思考', qwen: 'Qwen' };
const suggestions = ['这周有哪些截止事项？', '帮我查找近期校招', '帮我安排一周的申请准备'];

export function assistantView(repo) {
  const models = repo.auth.assistantModels ?? [];
  const available = repo.auth.user && models.length;
  return `<section class="assistant-panel" aria-labelledby="assistant-heading">
    <div class="assistant-heading">
      <div><h2 id="assistant-heading">PathFinder 智能体</h2><p>${available ? '交大模型会结合你的日程和院系资料回答。' : '查通知、理日程、规划申请'}</p></div>
      ${available ? '<button type="button" class="text-link" data-assistant-clear>新对话</button>' : ''}
    </div>
    ${available ? `<div class="assistant-thread" role="log" aria-label="智能体对话" aria-live="polite"></div>
      <form class="assistant-form">
        <label class="sr-only" for="assistant-message">发给智能体的消息</label>
        <textarea id="assistant-message" name="message" rows="2" maxlength="1200" placeholder="例如：周五前提醒我整理简历" required></textarea>
        <div class="assistant-actions">
          <div class="assistant-options"><label class="sr-only" for="assistant-model">模型</label>
            <select id="assistant-model" name="model">${models.filter((model) => Object.hasOwn(labels, model)).map((model) => `<option value="${model}">${labels[model]}</option>`).join('')}</select>
            <label class="assistant-auto"><input type="checkbox" name="auto"> 自动保存本次生成的待办和计划</label>
          </div>
          <button class="button primary" type="submit">发送</button>
        </div>
      </form><p class="assistant-status" role="status"></p>` : repo.auth.user ?
      '<p class="assistant-empty">智能体服务暂不可用。</p>' :
      '<p class="assistant-empty"><a class="text-link" href="#/login">登录</a>后可结合你的日程规划，并保存待办和申请计划。</p>'}
  </section>`;
}

function messageView(entry, index) {
  const sources = entry.sources?.flatMap(({ ref, title, url }) => {
    const link = safeExternalUrl(url);
    return link ? [`<li><a href="${h(link)}" target="_blank" rel="noopener noreferrer">[${h(ref)}] ${h(title)}</a></li>`] : [];
  }) ?? [];
  const actions = entry.actions ?? [];
  return `<article class="assistant-message" data-role="${entry.role}">
    <span class="sr-only">${entry.role === 'user' ? '你' : '智能体'}</span><p>${h(entry.content)}</p>
    ${sources.length ? `<ul class="assistant-sources" aria-label="来源">${sources.join('')}</ul>` : ''}
    ${actions.length ? `<div class="assistant-proposal"><ul>${actions.map(({ kind, title, date, category, tasks }) => `<li><span>${kind === 'plan' ? '申请计划' : h(todoCategories[category])}</span><strong>${h(title)}</strong>${date ? `<time datetime="${h(date)}">${h(date)}</time>` : ''}${tasks?.length ? `<ul class="assistant-plan-tasks">${tasks.map((task) => `<li><time datetime="${h(task.date)}">${h(task.date)}</time> ${h(task.name)}</li>`).join('')}</ul>` : ''}</li>`).join('')}</ul>
      ${entry.saved ? '<p class="assistant-saved">已保存至日程</p>' : `<button type="button" class="button secondary" data-assistant-save="${index}">保存 ${actions.length} 项</button>`}</div>` : ''}
  </article>`;
}

export function createAssistantPanel(repo, refresh) {
  const messages = [];
  let model = repo.auth.assistantModels?.[0];
  let busy = false;
  let status = '';
  let draft = '';
  let auto = false;
  let panel;

  function paint(scroll = false) {
    if (!panel?.isConnected) return;
    const thread = panel.querySelector('.assistant-thread');
    if (!thread) return;
    const form = panel.querySelector('form');
    thread.innerHTML = messages.length ? messages.map(messageView).join('') : `<div class="assistant-starters">${suggestions.map((label) => `<button type="button" data-assistant-suggestion="${h(label)}">${h(label)}</button>`).join('')}</div>`;
    form.elements.model.value = model;
    form.elements.message.value = draft;
    form.elements.auto.checked = auto;
    panel.querySelectorAll('button, select, textarea, input').forEach((control) => { control.disabled = busy; });
    panel.querySelector('.assistant-status').textContent = status;
    if (scroll) thread.scrollTop = thread.scrollHeight;
  }

  async function save(entry) {
    status = '正在保存到日程…';
    paint(true);
    await repo.applyAssistant(entry.actions);
    entry.saved = true;
    status = `已保存 ${entry.actions.length} 项。`;
    refresh(entry.actions.some(({ kind }) => kind === 'todo'));
  }

  return function bind(root) {
    panel = root.querySelector('.assistant-panel');
    const form = panel?.querySelector('form');
    if (!form) return;
    paint(true);
    form.addEventListener('input', () => {
      draft = form.elements.message.value;
      model = form.elements.model.value;
      auto = form.elements.auto.checked;
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = draft.trim();
      if (!message || busy) return;
      const history = messages.slice(-6).map(({ role, content }) => ({ role, content }));
      const request = { message, model, history };
      const saveAutomatically = auto;
      busy = true;
      draft = '';
      auto = false;
      messages.push({ role: 'user', content: message });
      status = '正在查找资料并整理回复…';
      paint(true);
      let answer;
      try {
        const result = await repo.askAssistant(request);
        answer = { role: 'assistant', content: result.reply, actions: result.actions, sources: result.sources };
        messages.push(answer);
        status = '';
        if (saveAutomatically && answer.actions.length) await save(answer);
      } catch (error) {
        if (!answer) { messages.pop(); draft = message; }
        status = error.message;
      } finally {
        busy = false;
        paint(true);
        if (form.isConnected) form.elements.message.focus({ preventScroll: true });
      }
    });
    panel.addEventListener('click', async (event) => {
      const button = event.target.closest('button');
      if (!button || busy) return;
      if (button.hasAttribute('data-assistant-clear')) {
        messages.length = 0;
        status = '';
        draft = '';
        auto = false;
        paint();
        form.elements.message.focus();
      } else if (button.dataset.assistantSuggestion) {
        draft = button.dataset.assistantSuggestion;
        form.elements.message.value = draft;
        form.elements.message.focus();
      } else if (button.hasAttribute('data-assistant-save')) {
        const entry = messages[Number(button.dataset.assistantSave)];
        if (!entry?.actions.length || entry.saved) return;
        busy = true;
        try { await save(entry); }
        catch (error) { status = error.message; }
        finally { busy = false; paint(true); }
      }
    });
  };
}
