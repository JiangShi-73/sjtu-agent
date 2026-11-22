import { canConfirm, createPlan, editTask, opportunityStatus, reschedule, validDate } from './domain.js';
import { openRepository } from './repository.js';
import { paintAbility } from './ability.js';
import { accountView, bindAccount } from './account.js';
import { bindDashboard, dashboardView } from './dashboard.js';
import { createAssistantPanel } from './assistant.js';
import { customCollege, customMajor, selectedMajorCategory } from './academic.js';
import { initializeTheme, toggleTheme } from './theme.js';
import {
  checkView, opportunitiesView, opportunityPath, pageHead, paintPlan, plansView,
  planView, profileView, shell, buttonLink, majorOptions,
} from './views.js';

const app = document.querySelector('#app');
const announcer = document.querySelector('#announcer');
const demo = new URLSearchParams(location.search).get('demo') === '1';
initializeTheme();

function announce(message) {
  announcer.textContent = message;
}

function decode(value) {
  try { return decodeURIComponent(value); }
  catch { return ''; }
}

function route() {
  const hash = location.hash.slice(1) || '/schedule';
  const [path, query] = hash.split('?');
  const matched = /^\/opportunities\/([^/]+)(?:\/(check|plan))?$/.exec(path);
  if (matched) return { page: matched[2] ?? 'detail', id: decode(matched[1]) };
  if (path === '/profile') return { page: 'profile', returnId: new URLSearchParams(query).get('return') };
  if (path === '/plans') return { page: 'plans' };
  if (path === '/login' || path === '/register') return { page: path.slice(1), campusError: new URLSearchParams(query).get('error') === 'campus' };
  if (path === '/schedule') return { page: 'schedule' };
  if (path === '/opportunities') return { page: 'list' };
  return { page: 'missing' };
}

function bindProfile(repo, returnOpportunity, refresh) {
  const form = app.querySelector('#profile-form');
  const tags = [...form.querySelectorAll('[name="experienceTags"]')];
  const none = form.elements.noExperience;
  const status = app.querySelector('#profile-save-status');
  const ability = app.querySelector('#ability-profile');
  const levels = [...form.querySelectorAll('[data-experience-level]')];
  const college = form.elements.collegeId;
  const major = form.elements.majorId;
  const saved = repo.getProfile();
  let affiliationEdited = false;

  function draft() {
    const data = new FormData(form);
    const number = (field) => form.elements[field].validity.valid && Number.isFinite(form.elements[field].valueAsNumber)
      ? form.elements[field].valueAsNumber : null;
    const selectedTags = data.getAll('experienceTags');
    return {
      collegeId: college.value || null,
      collegeText: college.value === customCollege ? form.elements.collegeText.value.trim() || null : null,
      majorId: major.value || null,
      majorText: major.value === customMajor ? form.elements.majorText.value.trim() || null : null,
      majorCategory: selectedMajorCategory(college.value, major.value) ??
        (!affiliationEdited && !saved?.collegeId ? saved?.majorCategory ?? null : null),
      graduationMonth: data.get('graduationMonth') || null,
      studyYear: data.get('studyYear') || null,
      rankPercent: number('rankPercent'),
      cet6Score: number('cet6Score'),
      experienceTags: selectedTags.length || none.checked ? selectedTags : null,
      experienceDepth: Object.fromEntries(selectedTags.flatMap((tag) => {
        const value = data.get(`depth-${tag}`);
        return value ? [[tag, Number(value)]] : [];
      })),
    };
  }

  function updateRadar() {
    for (const level of levels) {
      const selected = tags.some((tag) => tag.value === level.dataset.experienceLevel && tag.checked);
      level.hidden = !selected;
      level.querySelector('select').disabled = !selected;
    }
    paintAbility(ability, draft());
  }

  function updateCustomFields() {
    for (const [id, show] of [['custom-college', college.value === customCollege], ['custom-major', major.value === customMajor]]) {
      const label = form.querySelector('#' + id);
      label.hidden = !show;
      label.querySelector('input').required = show;
    }
  }
  function setMajor(value = '') {
    major.innerHTML = majorOptions(college.value, value);
    major.disabled = !college.value;
  }

  form.addEventListener('input', ({ target }) => {
    if (target === college || target === major) affiliationEdited = true;
    if (target === college) {
      form.elements.majorText.value = '';
      setMajor();
    }
    updateCustomFields();
    if (target === none && none.checked) tags.forEach((tag) => { tag.checked = false; });
    if (target.name === 'experienceTags' && target.checked) none.checked = false;
    status.textContent = '';
    updateRadar();
  });
  updateCustomFields();
  updateRadar();

  app.querySelector('#fill-profile').addEventListener('click', () => {
    const example = repo.examples.find(({ id }) => id === app.querySelector('#sample-profile').value);
    if (!example) return;
    affiliationEdited = true;
    college.value = example.collegeId ?? '';
    setMajor(example.majorId);
    updateCustomFields();
    for (const field of ['studyYear', 'graduationMonth', 'rankPercent', 'cet6Score']) {
      form.elements[field].value = example[field] ?? '';
    }
    for (const tag of tags) tag.checked = example.experienceTags?.includes(tag.value) ?? false;
    none.checked = example.experienceTags?.length === 0;
    for (const level of levels) level.querySelector('select').value = example.experienceDepth?.[level.dataset.experienceLevel] ?? '';
    updateRadar();
    status.textContent = '已填入示例。保存后可用于条件核验。';
  });

  app.querySelector('#clear-profile').addEventListener('click', () => {
    repo.clearProfile();
    refresh(true);
    app.querySelector('#clear-profile').focus();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    repo.saveProfile(draft());
    try { await repo.flush(); }
    catch (error) { status.textContent = error.message; return; }
    const message = repo.persistent ? '个人背景已保存。' : '已在当前会话更新，刷新后可能丢失。';
    if (returnOpportunity) {
      announce(message);
      location.hash = opportunityPath(returnOpportunity, '/check');
    } else status.textContent = message;
  });
  return updateRadar;
}

function bindPlan(repo, opportunity, plan, refresh) {
  const targetForm = app.querySelector('#target-form');
  if (targetForm) {
    targetForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const target = new FormData(targetForm).get('targetDate');
      if (!validDate(target) || target < repo.today()) return;
      repo.savePlan(createPlan(opportunity, target));
      refresh();
      app.querySelector('#main-content').focus();
    });
    return;
  }
  const form = app.querySelector('#plan-form');
  if (!form) return;

  let current = plan;
  const invalid = new Set();
  const confirmable = () => !invalid.size && canConfirm(current, opportunity, repo.today());
  const paint = (edited) => paintPlan(form, current, repo, confirmable(), edited, invalid);
  paint();

  form.addEventListener('input', ({ target: input }) => {
    if (!(input instanceof HTMLInputElement)) return;
    if (!input.validity.valid || (input.type === 'date' && !validDate(input.value))) {
      invalid.add(input);
      current = { ...current, confirmed: false };
    } else {
      invalid.delete(input);
      if (input.name === 'targetDate') current = reschedule(current, input.value);
      else {
        const id = input.closest('[data-task]').dataset.task;
        const field = input.dataset.field;
        current = editTask(current, id, field, field === 'leadDays' ? input.valueAsNumber : input.value);
      }
    }
    repo.savePlan(current);
    paint(input);
  });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!confirmable()) return;
    current = { ...current, confirmed: true };
    repo.savePlan(current);
    paint();
    form.querySelector('#download-plan').focus();
  });

  form.querySelector('#download-plan').addEventListener('click', () => {
    if (!confirmable()) { paint(); return; }
    const calendar = repo.calendar(current, opportunity);
    if (!calendar) return;
    const url = URL.createObjectURL(new Blob([calendar], { type: 'text/calendar;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `pathfinder-${opportunity.id}.ics`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    announce('已生成日历下载。');
  });
}

try {
  const repo = await openRepository({ demo });
  function syncStatus() {
    const status = app.querySelector('#sync-status');
    if (!status) return;
    status.textContent = repo.syncError || (repo.saving ? '正在保存…' : '');
    app.querySelector('#retry-sync').hidden = !repo.syncError;
  }
  window.addEventListener('pathfinder-sync', syncStatus);
  window.addEventListener('storage', (event) => {
    if (event.key === 'pathfinder:auth-change') location.reload();
  });
  window.addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });
  let filter = 'all';
  let deadlineFilter = 'all';
  let selectedDay = '';
  let completedOpen = false;
  let lastListScroll = 0;
  let previousPage = null;
  const bindAssistant = createAssistantPanel(repo, (addedTodo) => {
    if (addedTodo) { deadlineFilter = 'all'; selectedDay = ''; }
    if (route().page === 'schedule') render();
  });

  function render(cleared = false, navigate = false) {
    const { page, id, returnId, campusError } = route();
    const opportunity = repo.getOpportunity(id);
    const returnOpportunity = repo.getOpportunity(returnId);
    let content;
    let active = 'opportunities';
    let plan = null;

    if (page === 'login' || page === 'register') {
      active = 'account';
      content = accountView(repo, page === 'register', campusError);
    } else if (page === 'schedule') {
      active = 'schedule';
      content = dashboardView(repo, deadlineFilter, completedOpen, selectedDay);
    } else if (page === 'profile') {
      active = 'profile';
      content = profileView(repo, returnOpportunity, cleared);
    } else if (page === 'plans') {
      active = 'plans';
      content = plansView(repo);
    } else if (page === 'list' || (page === 'detail' && opportunity)) {
      if (opportunity && filter !== 'all' && filter !== opportunity.type) filter = opportunity.type;
      content = opportunitiesView(repo, filter, opportunity);
    } else if (page === 'check' && opportunity) {
      content = checkView(repo, opportunity);
    } else if (page === 'plan' && opportunity) {
      active = 'plans';
      plan = repo.getPlan(id);
      if (opportunityStatus(opportunity, repo.today()) === 'open') {
        if (!plan) {
          plan = createPlan(opportunity, opportunity.deadlineDate);
          repo.savePlan(plan);
        } else if (plan.targetDate !== opportunity.deadlineDate || plan.targetDateSource !== 'official') {
          plan = { ...reschedule(plan, opportunity.deadlineDate), targetDateSource: 'official' };
          repo.savePlan(plan);
        }
      }
      content = planView(repo, opportunity, plan);
    } else {
      content = `${pageHead('找不到这个页面', '地址可能已变更。')}<div class="panel content-panel">${buttonLink('#/schedule', '返回日程导视')}</div>`;
    }

    app.innerHTML = shell(content, active, repo, demo);
    syncStatus();
    if (active === 'account') bindAccount(app, repo, page === 'register');
    app.querySelector('#retry-sync').addEventListener('click', () => repo.flush().catch(() => {}));
    app.setAttribute('aria-busy', 'false');
    if (page === 'schedule') bindDashboard(app, repo, {
      refresh: () => render(),
      setFilter: (value) => { deadlineFilter = value; },
      setDay: (value) => { selectedDay = selectedDay === value ? '' : value; },
      keepCompleted: (value) => { completedOpen = value; },
      announce,
    });
    if (page === 'schedule') bindAssistant(app);
    const refreshProfileRadar = page === 'profile' ? bindProfile(repo, returnOpportunity, (flag) => render(flag)) : null;
    const checkRadar = app.querySelector('#ability-check');
    if (checkRadar) paintAbility(checkRadar, repo.getProfile());
    app.querySelector('#theme-toggle').addEventListener('click', ({ currentTarget }) => {
      toggleTheme(currentTarget);
      if (refreshProfileRadar) refreshProfileRadar();
      if (checkRadar) paintAbility(checkRadar, repo.getProfile());
    });
    if (page === 'plan' && opportunity) bindPlan(repo, opportunity, plan, () => render());

    if (navigate) {
      if (previousPage === 'list' && page === 'detail') lastListScroll = window.scrollY;
      const scroll = previousPage === 'detail' && page === 'list' ? lastListScroll : 0;
      app.querySelector('#main-content').focus({ preventScroll: true });
      window.scrollTo(0, scroll);
    }
    previousPage = page;
  }

  app.addEventListener('click', (event) => {
    const filterButton = event.target.closest('[data-filter]');
    if (filterButton) {
      filter = filterButton.dataset.filter;
      if (route().page === 'detail') location.hash = '#/opportunities';
      else {
        render();
        app.querySelector(`.filters [data-filter="${filter}"]`)?.focus();
      }
    }
    const remove = event.target.closest('[data-delete]');
    if (remove && window.confirm('删除这份申请计划？')) {
      repo.clearPlan(remove.dataset.delete);
      render();
      app.querySelector('#main-content').focus();
      announce('计划已删除。');
    }
  });
  window.addEventListener('hashchange', () => render(false, true));
  render();
} catch (error) {
  app.setAttribute('aria-busy', 'false');
  app.innerHTML = '<main class="load-error"><h1>暂时无法加载 PathFinder</h1><p>请通过本地开发地址或部署网址打开，并刷新页面。</p></main>';
  console.error(error);
}
