import { assess, emptyProfile, exportCalendar, todayInChina } from './domain.js';
import { campusCalendarUrl, campusNoticeUrl, campusTerms } from './campus.js';
import { publicDataReviewedOn, publicOpportunities } from './public-data.js';
import { api, session } from './account.js';
import { collegeNames, majorNames, selectedMajorCategory } from './academic.js';
import { collegeBulletins } from './college-bulletins.js';
import { noticesForCollege } from './notices.js';

export async function openRepository({ demo = false } = {}) {
  const response = await fetch(new URL('./mvp-demo.json', import.meta.url));
  if (!response.ok) throw new Error('机会样例加载失败');
  const fixture = await response.json();
  const auth = await session();
  const endpoint = 'data' + (demo ? '?demo=1' : '');
  const key = `pathfinder:v1:${demo ? 'demo' : 'local'}`;
  const state = { profile: null, plans: {}, todos: [] };
  let persistent = true;
  if (auth.user) {
    Object.assign(state, await api(endpoint, { user: auth.user.id }));
  } else try {
    const stored = window.localStorage.getItem(key);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (parsed?.profile && typeof parsed.profile === 'object') state.profile = parsed.profile;
      if (parsed?.plans && typeof parsed.plans === 'object' && !Array.isArray(parsed.plans)) state.plans = parsed.plans;
      if (Array.isArray(parsed?.todos)) state.todos = parsed.todos;
    }
  } catch {
    persistent = false;
  }

  const pending = [];
  let sending;
  let syncError = '';
  const notify = () => window.dispatchEvent(new Event('pathfinder-sync'));
  function flush() {
    if (sending) return sending;
    sending = (async () => {
      syncError = '';
      while (pending.length) {
        await api(endpoint, { method: 'POST', user: auth.user.id, value: pending[0] });
        pending.shift();
      }
    })().catch((error) => { syncError = error.message; throw error; })
      .finally(() => { sending = null; notify(); });
    return sending;
  }
  function save(kind, id, value) {
    if (auth.user) {
      pending.push({ kind, id, value: structuredClone(value) });
      notify();
      flush().catch(() => {});
      return;
    }
    try {
      window.localStorage.setItem(key, JSON.stringify(state));
      persistent = true;
    } catch {
      persistent = false;
    }
  }

  const clock = () => demo ? fixture.referenceDate : todayInChina();
  const opportunities = demo ? fixture.opportunities : publicOpportunities;
  function collegeFeed() {
    const collegeId = state.profile?.collegeId;
    return !demo && Object.hasOwn(collegeBulletins, collegeId) ? collegeBulletins[collegeId] : null;
  }
  window.addEventListener('beforeunload', (event) => {
    if (pending.length) { event.preventDefault(); event.returnValue = ''; }
  });
  return {
    auth, flush,
    async askAssistant(value) {
      await flush();
      return api('assistant' + (demo ? '?demo=1' : ''), { method: 'POST', user: auth.user.id, value });
    },
    async applyAssistant(actions) {
      await flush();
      const { created } = await api('assistant/apply' + (demo ? '?demo=1' : ''), {
        method: 'POST', user: auth.user.id, value: { actions },
      });
      for (const { kind, value } of created) {
        if (kind === 'todo' && !state.todos.some(({ id }) => id === value.id)) state.todos.push(value);
        if (kind === 'plan' && !Object.hasOwn(state.plans, value.opportunityId)) state.plans[value.opportunityId] = value;
      }
      return created;
    },
    get syncError() { return syncError; },
    get saving() { return pending.length > 0; },
    options: { ...fixture.options, collegeId: collegeNames, majorId: majorNames },
    examples: fixture.profiles,
    opportunities,
    reviewedOn: demo ? null : publicDataReviewedOn,
    terms: campusTerms,
    noticeUrl: campusNoticeUrl,
    calendarUrl: campusCalendarUrl,
    get persistent() { return persistent; },
    today: clock,
    now: () => demo ? new Date(fixture.referenceDate + 'T00:00:00+08:00') : new Date(),
    getOpportunity(id) {
      return opportunities.find((item) => item.id === id)
        ?? (Object.hasOwn(state.plans, id) ? fixture.opportunities.find((item) => item.id === id) : undefined);
    },
    getProfile: () => state.profile,
    saveProfile(profile) { state.profile = profile; save('profile', 'me', profile); },
    clearProfile() { state.profile = null; save('profile', 'me', null); },
    getPlans: () => Object.values(state.plans),
    getPlan: (id) => Object.hasOwn(state.plans, id) ? state.plans[id] : null,
    savePlan(plan) { state.plans[plan.opportunityId] = plan; save('plan', plan.opportunityId, plan); },
    clearPlan(id) { delete state.plans[id]; save('plan', id, null); },
    getCollegeFeed: collegeFeed,
    getNotices() {
      return demo ? fixture.notices ?? [] : noticesForCollege(state.profile?.collegeId);
    },
    getTodos: () => state.todos,
    saveTodo(todo) {
      const index = state.todos.findIndex(({ id }) => id === todo.id);
      if (index === -1) state.todos.push(todo);
      else state.todos[index] = todo;
      save('todo', todo.id, todo);
    },
    clearTodo(id) { state.todos = state.todos.filter((todo) => todo.id !== id); save('todo', id, null); },
    assess(opportunity) {
      const profile = state.profile ?? emptyProfile;
      const majorCategory = profile.collegeId ? selectedMajorCategory(profile.collegeId, profile.majorId) : profile.majorCategory;
      return assess(opportunity, { ...profile, majorCategory });
    },
    calendar: (plan, opportunity) => exportCalendar(plan, opportunity, clock()),
  };
}
