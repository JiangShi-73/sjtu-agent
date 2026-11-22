import { validDate } from './domain.js';

export const todoCategories = {
  course: '课程', academic: '教务', campus: '校园', application: '升学', career: '求职', personal: '生活',
};

export function daysBetween(start, end) {
  return (Date.parse(end) - Date.parse(start)) / 86_400_000;
}

export function currentTerm(terms, today) {
  const term = terms.find(({ start, end }) => start <= today && today <= end);
  return term ? { ...term, week: Math.floor(daysBetween(term.start, today) / 7) + 1 } : null;
}

export function noticeExpired({ dueDate, dueTime }, today, now) {
  if (!validDate(dueDate)) return false;
  if (dueDate !== today) return dueDate < today;
  return Boolean(dueTime && now.getTime() >= Date.parse(dueDate + 'T' + dueTime + ':00+08:00'));
}

function planEntries(repo, today) {
  return repo.getPlans().flatMap((plan) => {
    const opportunity = repo.getOpportunity(plan.opportunityId);
    if (!opportunity || !validDate(plan.targetDate) || plan.targetDate < today) return [];
    if (opportunity.deadlineDate && opportunity.deadlineDate < today) return [];
    const confirmation = plan.confirmed ? '已确认' : '待确认';
    const status = opportunity.source.kind === 'demo' ? '演示 · ' + confirmation : confirmation;
    const href = `#/opportunities/${encodeURIComponent(opportunity.id)}/plan`;
    const kind = opportunity.type === 'summer-camp' ? 'application' : 'career';
    const items = plan.tasks.map((task) => ({ title: task.name, date: task.date, source: `${opportunity.title} · ${status}`, href, kind }));
    items.push({ title: opportunity.title, date: plan.targetDate, source: `申请目标日 · ${status}`, href, kind, duplicateDeadline: opportunity.deadlineDate === plan.targetDate });
    return items.filter(({ date }) => validDate(date));
  });
}

export function scheduleDeadlines(repo, today, now = new Date()) {
  const opportunities = repo.opportunities.flatMap((opportunity) => {
    const { id, title, deadlineDate, type, source } = opportunity;
    if (!validDate(deadlineDate) || deadlineDate < today) return [];
    return [{ title, date: deadlineDate, kind: type === 'summer-camp' ? 'application' : 'career', source: source.kind === 'demo' ? '演示机会' : opportunity.publisher, href: `#/opportunities/${encodeURIComponent(id)}` }];
  });
  const notices = repo.getNotices().flatMap((notice) => {
    const { title, dueDate, dueTime, publishedDate, category, demo, publisher, url } = notice;
    if (!validDate(dueDate) || noticeExpired(notice, today, now) || !validDate(publishedDate) || publishedDate > today) return [];
    return [{ title, date: dueDate, time: dueTime, kind: category ?? 'academic', source: demo ? `演示 · ${publisher}` : publisher, href: url, external: true }];
  });
  const personal = repo.getTodos().flatMap(({ id, title, date, category, done }) => {
    if (done) return [];
    return [{ title, date: validDate(date) ? date : null, kind: category, source: '个人待办', todoId: id }];
  });
  const plans = planEntries(repo, today).filter(({ duplicateDeadline }) => !duplicateDeadline);
  return [...opportunities, ...notices, ...plans, ...personal].sort((a, b) =>
    (a.date ?? '9999-12-31').localeCompare(b.date ?? '9999-12-31') ||
    (a.time ?? '24:00').localeCompare(b.time ?? '24:00'));
}

export function futureAgenda(repo, today) {
  const milestones = repo.terms.flatMap(({ sourceUrl, milestones }) => milestones.map(({ title, date }) => ({
    title, date, source: '学校校历', href: sourceUrl, external: true,
  })));
  return [...milestones, ...planEntries(repo, today)].filter(({ date }) => validDate(date) && date >= today)
    .sort((a, b) => a.date.localeCompare(b.date));
}
