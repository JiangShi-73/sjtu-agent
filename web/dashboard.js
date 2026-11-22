import { shiftDate, validDate } from './domain.js';
import { currentTerm, daysBetween, futureAgenda, noticeExpired, scheduleDeadlines, todoCategories } from './schedule.js';
import { escapeHtml as h, guestGuide, safeExternalUrl } from './views.js';
import { colleges, departmentDirectoryUrl } from './academic.js';
import { assistantView } from './assistant.js';

const categories = [['all', '全部'], ...Object.entries(todoCategories)];
const fullDate = new Intl.DateTimeFormat('zh-CN', { timeZone: 'UTC', dateStyle: 'long' });
const shortDate = new Intl.DateTimeFormat('zh-CN', { timeZone: 'UTC', month: 'long', day: 'numeric' });
const weekday = new Intl.DateTimeFormat('zh-CN', { timeZone: 'UTC', weekday: 'long' });

function dateText(date, today) {
  if (!validDate(date)) return '未设置截止日';
  const format = date.slice(0, 4) === today.slice(0, 4) ? shortDate : fullDate;
  return `<time datetime="${h(date)}">${h(format.format(new Date(date)))}</time>`;
}

function titleLink({ title, href, external }) {
  if (external) {
    const url = safeExternalUrl(href);
    if (url) return `<a class="schedule-link" href="${h(url)}" target="_blank" rel="noopener noreferrer">${h(title)}</a>`;
  } else if (href) {
    return `<a class="schedule-link" href="${h(href)}">${h(title)}</a>`;
  }
  return h(title);
}

function deadlineBadge(date, today) {
  if (!validDate(date)) return '';
  const days = daysBetween(today, date);
  if (days < 0) return `<span class="badge unmet">逾期 ${-days} 天</span>`;
  if (days === 0) return '<span class="badge unknown">今天截止</span>';
  if (days <= 7) return `<span class="badge unknown">剩 ${days} 天</span>`;
  return '';
}

function categoryLabel(kind) {
  const category = Object.hasOwn(todoCategories, kind) ? kind : 'personal';
  return `<span class="item-category" data-category="${category}">${todoCategories[category]}</span>`;
}

function todoToggle(id, title, done) {
  return `<label class="todo-toggle"><input type="checkbox" data-todo-toggle="${h(id)}" ${done ? 'checked' : ''}><span class="sr-only">${done ? '重新打开' : '完成'} ${h(title)}</span></label>`;
}

function todoEdit(id, title) {
  return `<button type="button" class="text-link todo-edit" data-todo-edit="${h(id)}" aria-label="编辑 ${h(title)}">编辑</button>`;
}

function deadlineList(items, today) {
  if (!items.length) return '<p class="schedule-empty">此类别没有待处理的截止事项。</p>';
  const rows = items.map((item) => `<li class="deadline-row">
    <div class="schedule-date">${dateText(item.date, today)}</div>
    <div class="schedule-item"><h3>${titleLink(item)}</h3><p class="item-meta">${categoryLabel(item.kind)} <span>${h(item.source)}${item.time ? ` · ${h(item.time)} 截止` : ''}</span></p></div>
    <div class="deadline-actions">${deadlineBadge(item.date, today)}${item.todoId ? todoToggle(item.todoId, item.title, false) + todoEdit(item.todoId, item.title) : ''}</div>
  </li>`).join('');
  return `<ul class="schedule-list">${rows}</ul>`;
}

function todoList(items, today) {
  return `<ul class="schedule-list todo-list">${items.map((item) => `<li class="todo-row ${item.done ? 'done' : ''}">
    ${todoToggle(item.id, item.title, item.done)}
    <div class="schedule-item"><h3>${h(item.title)}</h3><p class="item-meta">${categoryLabel(item.category)} ${dateText(item.date, today)}</p></div>
    ${todoEdit(item.id, item.title)}
  </li>`).join('')}</ul>`;
}

function todoDialog() {
  return `<dialog class="todo-dialog" id="todo-dialog" aria-labelledby="todo-dialog-title">
    <form id="todo-form">
      <div class="dash-card-head"><h2 id="todo-dialog-title">新建待办</h2><button type="button" class="dialog-close" data-todo-cancel aria-label="关闭">×</button></div>
      <label>事项名称<input name="title" type="text" maxlength="80" required placeholder="例如：提交课程期末报告" autofocus></label>
      <div class="form-fields">
        <label>类别<select name="category">${Object.entries(todoCategories).map(([id, label]) => `<option value="${id}">${label}</option>`).join('')}</select></label>
        <label>截止日期（可选）<input name="date" type="date" max="9999-12-31"></label>
      </div>
      <div class="dialog-actions"><button class="text-link todo-delete" type="button" data-todo-delete hidden>删除待办</button><div class="strip-buttons"><button type="button" class="button secondary" data-todo-cancel>取消</button><button type="submit" class="button primary">保存待办</button></div></div>
    </form>
  </dialog>`;
}

export function dashboardView(repo, filter, completedOpen, selectedDay) {
  const today = repo.today();
  const now = repo.now();
  const term = currentTerm(repo.terms, today);
  const deadlines = scheduleDeadlines(repo, today, now);
  const week = Array.from({ length: 7 }, (_, i) => {
    const date = shiftDate(today, i);
    return { date, count: deadlines.filter((event) => event.date === date).length };
  });
  const maxCount = Math.max(1, ...week.map((d) => d.count));
  const future = futureAgenda(repo, today);
  const futureRows = (items) => `<ul class="future-list">${items.map((item) => `<li>${dateText(item.date, today)}${titleLink(item)}</li>`).join('')}</ul>`;
  const todos = [...repo.getTodos()].sort((a, b) => (a.date || '9999-12-31').localeCompare(b.date || '9999-12-31'));
  const completed = todos.filter(({ done }) => done);
  const collegeId = repo.getProfile()?.collegeId;
  const college = Object.hasOwn(colleges, collegeId) ? colleges[collegeId] : null;
  const collegeFeed = repo.getCollegeFeed();
  const isOwnNotice = (notice) => Boolean(college && notice.collegeId === collegeId);
  const notices = repo.getNotices().filter(({ publishedDate }) => validDate(publishedDate) && publishedDate <= today)
    .sort((a, b) => Number(isOwnNotice(b)) - Number(isOwnNotice(a)) || b.publishedDate.localeCompare(a.publishedDate));
  const noticeRow = (notice) => {
    const deadline = noticeExpired(notice, today, now) ? ' · 已截止' : '';
    return `<li><h3>${titleLink({ ...notice, href: notice.url, external: true })}</h3><p>${isOwnNotice(notice) ? '本学院 · ' : ''}${h(notice.publisher)} · ${dateText(notice.publishedDate, today)}${notice.demo ? ' · 演示通知' : ''}${deadline}</p></li>`;
  };
  return `<div class="dashboard">
    <header class="dashboard-banner">
      <div><p class="dash-date">${dateText(today, '')} · ${h(weekday.format(new Date(today)))}</p><h1>日程导视</h1>
        <a class="term-link" href="${h(term?.sourceUrl ?? repo.calendarUrl)}" target="_blank" rel="noopener noreferrer">${term ? h(term.name) + ' · 第 ' + term.week + ' 周' : '查看学校校历'}</a></div>
      <section class="week-overview" aria-label="未来七天截止事项"><div class="week-caption"><span>七日截止分布</span><strong>${week.reduce((sum, day) => sum + day.count, 0)} 项</strong></div>
        <div class="week-bars">${week.map(({ date, count }, i) => `<button type="button" data-day="${date}" aria-pressed="${selectedDay === date}" aria-label="${date}，${count} 项截止" title="${date} · ${count} 项"><span class="bar-space"><i style="height:${count / maxCount * 100}%"></i></span><span>${i === 0 ? '今天' : date.slice(-2) + '日'}</span></button>`).join('')}</div></section>
    </header>
    ${guestGuide(repo)}
    ${assistantView(repo)}
    <div class="dashboard-grid">
      <section class="panel dash-card dash-deadlines" aria-labelledby="deadline-heading">
        <div class="dash-card-head"><h2 id="deadline-heading">截止事项</h2><div class="deadline-controls"><select class="compact-select" aria-label="筛选截止事项类别" data-deadline-category>${categories.map(([id, name]) => `<option value="${id}" ${filter === id ? 'selected' : ''}>${name}</option>`).join('')}</select><button type="button" class="button primary add-todo" data-todo-add>新建待办</button></div></div>
        ${selectedDay ? `<button class="day-reset" data-day="" aria-label="清除日期筛选">${h(selectedDay)} ×</button>` : ''}
        ${deadlineList(deadlines.filter(({ kind, date }) => (filter === 'all' || kind === filter) && (!selectedDay || selectedDay === date)), today)}
        ${completed.length ? `<details class="completed-todos" id="completed-todos" ${completedOpen ? 'open' : ''}><summary>已完成 ${completed.length} 项</summary>${todoList(completed, today)}</details>` : ''}
        ${repo.persistent ? '' : '<p class="inline-notice">当前浏览器存储不可用，刷新后可能丢失待办。</p>'}
      </section>
      <section class="panel dash-card dash-notices" aria-labelledby="notice-heading">
        <div class="dash-card-head"><h2 id="notice-heading">通知</h2><a class="text-link" href="${h(repo.noticeUrl)}" target="_blank" rel="noopener noreferrer">教务处通知</a></div>
        <p class="notice-scope">${college ? `${h(college.name)} · <a href="${h(collegeFeed?.source ?? college.noticeUrl ?? departmentDirectoryUrl)}" target="_blank" rel="noopener noreferrer">${collegeFeed?.source || college.noticeUrl ? '学院官网' : '院系官网目录'}</a>${collegeFeed?.checkedOn ? ` · 收录于 ${dateText(collegeFeed.checkedOn, today)}` : ''}${notices.some(isOwnNotice) ? '' : ' · 暂无已收录的本学院通知'}` : '<a href="#/profile">设置学院，查看学院通知</a>'}</p>
        ${notices.length ? `<ul class="schedule-list notice-list">${notices.slice(0, 5).map(noticeRow).join('')}</ul>` : '<p class="schedule-empty">暂无已接入的学院或教务通知。</p>'}
        ${notices.length > 5 ? `<details class="completed-todos"><summary>更多通知（${notices.length - 5} 条）</summary><ul class="schedule-list notice-list">${notices.slice(5).map(noticeRow).join('')}</ul></details>` : ''}
      </section>
      <section class="panel dash-card dash-future" aria-labelledby="future-heading">
        <div class="dash-card-head"><h2 id="future-heading">未来计划</h2><a class="text-link" href="#/plans">申请计划</a></div>
        ${future.length ? futureRows(future.slice(0, 2)) : '<p class="schedule-empty">暂无校历安排或申请计划。</p>'}
        ${future.length > 2 ? `<details class="future-more"><summary>展开其余 ${future.length - 2} 项</summary>${futureRows(future.slice(2))}</details>` : ''}
      </section>
    </div>
    ${todoDialog()}
  </div>`;
}

export function bindDashboard(root, repo, { refresh, setFilter, setDay, keepCompleted, announce }) {
  const dashboard = root.querySelector('.dashboard');
  const dialog = dashboard.querySelector('#todo-dialog');
  const form = dialog.querySelector('form');
  const completed = dashboard.querySelector('#completed-todos');
  let current = null;

  const refreshDashboard = (target, message, showCompleted = completed?.open ?? false) => {
    keepCompleted(showCompleted);
    refresh();
    (root.querySelector(target) ?? root.querySelector('[data-todo-add]'))?.focus({ preventScroll: true });
    if (message) announce(message);
  };

  function showEditor(todo = null) {
    current = todo;
    form.reset();
    form.elements.title.setCustomValidity('');
    form.elements.date.setCustomValidity('');
    form.elements.title.value = todo?.title ?? '';
    form.elements.category.value = todo?.category ?? 'course';
    form.elements.date.value = todo?.date ?? '';
    dialog.querySelector('#todo-dialog-title').textContent = todo ? '编辑待办' : '新建待办';
    dialog.querySelector('[data-todo-delete]').hidden = !todo;
    dialog.showModal();
  }

  dashboard.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-day')) {
      setDay(button.dataset.day);
      refreshDashboard(button.dataset.day ? `[data-day="${button.dataset.day}"]` : '[data-deadline-category]');
    } else if (button.hasAttribute('data-todo-add')) showEditor();
    else if (button.hasAttribute('data-todo-edit')) {
      const todo = repo.getTodos().find(({ id }) => id === button.dataset.todoEdit);
      if (todo) showEditor(todo);
    } else if (button.hasAttribute('data-todo-cancel')) dialog.close();
    else if (button.hasAttribute('data-todo-delete') && current && window.confirm('删除这条待办？')) {
      repo.clearTodo(current.id);
      dialog.close();
      refreshDashboard('[data-todo-add]', '待办已删除。');
    }
  });

  dashboard.addEventListener('change', (event) => {
    if (event.target.hasAttribute('data-deadline-category')) {
      setFilter(event.target.value);
      refreshDashboard('[data-deadline-category]');
      return;
    }
    const id = event.target.dataset.todoToggle;
    if (!id) return;
    const todo = repo.getTodos().find((item) => item.id === id);
    if (!todo) return;
    const done = event.target.checked;
    repo.saveTodo({ ...todo, done });
    if (!done) {
      setFilter('all');
      setDay('');
    }
    refreshDashboard(`[data-todo-toggle="${CSS.escape(id)}"]`, done ? '待办已完成。' : '待办已重新打开。', done || completed?.open);
  });

  form.addEventListener('input', (event) => event.target.setCustomValidity?.(''));
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const title = form.elements.title.value.trim();
    const date = form.elements.date.value;
    if (!title) {
      form.elements.title.setCustomValidity('请输入事项名称。');
      form.elements.title.reportValidity();
      return;
    }
    if (date && !validDate(date)) {
      form.elements.date.setCustomValidity('请输入有效的截止日期。');
      form.elements.date.reportValidity();
      return;
    }
    // 学校内网可使用 HTTP。getRandomValues 同样适用于此场景。
    const id = current?.id ?? Array.from(crypto.getRandomValues(new Uint32Array(4)), (part) => part.toString(36)).join('-');
    repo.saveTodo({ id, title, date: date || null, category: form.elements.category.value, done: current?.done ?? false });
    setFilter('all');
    setDay('');
    dialog.close();
    refreshDashboard(`[data-todo-edit="${CSS.escape(id)}"]`, '待办已保存。');
  });
}
