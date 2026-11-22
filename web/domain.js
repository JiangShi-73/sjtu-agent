export const emptyProfile = {
  collegeId: null,
  majorId: null,
  graduationMonth: null,
  majorCategory: null,
  studyYear: null,
  rankPercent: null,
  cet6Score: null,
  experienceTags: null,
};

const chinaDate = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
});

export function todayInChina(now = new Date()) {
  const { year, month, day } = Object.fromEntries(
    chinaDate.formatToParts(now).map(({ type, value }) => [type, value]),
  );
  return `${year}-${month}-${day}`;
}

export function validDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function shiftDate(value, days) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function opportunityStatus(opportunity, today) {
  if (!opportunity.deadlineDate) return 'undated';
  return opportunity.deadlineDate < today ? 'expired' : 'open';
}

function compare(operator, userValue, requiredValue) {
  switch (operator) {
    case 'lte': return typeof userValue === 'number' && userValue <= requiredValue;
    case 'gte': return typeof userValue === 'number' && userValue >= requiredValue;
    case 'in': return requiredValue.includes(userValue);
    case 'month-range':
      return /^\d{4}-(0[1-9]|1[0-2])$/.test(userValue) ? requiredValue[0] <= userValue && userValue <= requiredValue[1] : null;
    case 'includes': return Array.isArray(userValue) && userValue.includes(requiredValue);
    default: return null;
  }
}

export function assess(opportunity, profile = emptyProfile) {
  const items = opportunity.requirements.map((requirement) => {
    const userValue = profile[requirement.field] ?? null;
    const outcome = userValue === null ? null : compare(requirement.operator, userValue, requirement.value);
    if (outcome === null) {
      const explanation = requirement.operator === 'manual'
        ? '请按公告自行确认这项条件。'
        : '补充个人背景后可再次核对。';
      return { requirementId: requirement.id, userValue, status: 'unknown', explanation };
    }
    if (requirement.kind === 'soft') {
      return {
        requirementId: requirement.id,
        userValue,
        status: outcome ? 'related' : 'improve',
        explanation: outcome ? '可在申请材料中写明你的工作和成果。' : '可回顾是否有相关课程、项目或实践。',
      };
    }
    return {
      requirementId: requirement.id,
      userValue,
      status: outcome ? 'met' : 'unmet',
      explanation: outcome ? '你填写的信息符合这项要求。' : '你填写的信息与公告要求存在差距。',
    };
  });
  const hardItems = items.filter((_, index) => opportunity.requirements[index].kind === 'hard');
  let summary = 'met';
  if (hardItems.some(({ status }) => status === 'unmet')) summary = 'unmet';
  else if (!hardItems.length || hardItems.some(({ status }) => status === 'unknown')) summary = 'unknown';
  return { opportunityId: opportunity.id, summary, items };
}

export function createPlan(opportunity, targetDate) {
  return {
    opportunityId: opportunity.id,
    targetDate,
    targetDateSource: opportunity.deadlineDate ? 'official' : 'user',
    confirmed: false,
    tasks: opportunity.materials.map(({ id, name, suggestedLeadDays }) => ({
      id, name, leadDays: suggestedLeadDays, date: shiftDate(targetDate, -suggestedLeadDays), manual: false,
    })),
  };
}

export function reschedule(plan, targetDate) {
  const tasks = plan.tasks.map((task) => task.manual ? task : {
    ...task, date: shiftDate(targetDate, -task.leadDays),
  });
  return { ...plan, targetDate, tasks, confirmed: false };
}

export function editTask(plan, id, field, value) {
  const tasks = plan.tasks.map((task) => {
    if (task.id !== id) return task;
    if (field === 'date') return { ...task, date: value, manual: true };
    return { ...task, leadDays: value, date: shiftDate(plan.targetDate, -value), manual: false };
  });
  return { ...plan, tasks, confirmed: false };
}

export function taskIssue(task, targetDate, today) {
  if (!validDate(task.date)) return 'invalid';
  if (task.date > targetDate) return 'late';
  if (task.date < today) return 'past';
  return null;
}

export function canConfirm(plan, opportunity, today) {
  if (!validDate(plan.targetDate) || plan.targetDate < today) return false;
  if (opportunity.deadlineDate && plan.targetDate !== opportunity.deadlineDate) return false;
  return plan.tasks.every((task) => {
    const issue = taskIssue(task, plan.targetDate, today);
    return issue !== 'invalid' && issue !== 'late';
  });
}

function calendarText(value) {
  return value.replaceAll('\\', '\\\\').replace(/\r\n|\r|\n/g, '\\n')
    .replaceAll(';', '\\;').replaceAll(',', '\\,');
}

function foldLine(line) {
  const encoder = new TextEncoder();
  const lines = [];
  let part = '';
  let size = 0;
  for (const character of line) {
    const bytes = encoder.encode(character).length;
    if (size + bytes > 75) {
      lines.push(part);
      part = ' ';
      size = 1;
    }
    part += character;
    size += bytes;
  }
  return [...lines, part].join('\r\n');
}

export function exportCalendar(plan, opportunity, today, now = new Date()) {
  if (!plan.confirmed || !canConfirm(plan, opportunity, today)) return null;
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  const events = [
    ...plan.tasks.map((task) => ({ ...task, id: `task-${task.id}` })),
    { id: 'submission', name: '核对并提交申请', date: plan.targetDate },
  ];
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//PathFinder SJTU//Application Plan//ZH', 'CALSCALE:GREGORIAN'];
  for (const event of events) {
    lines.push('BEGIN:VEVENT', `UID:${opportunity.id}-${event.id}@pathfinder.local`, `DTSTAMP:${stamp}`,
      `DTSTART;VALUE=DATE:${event.date.replaceAll('-', '')}`,
      `DTEND;VALUE=DATE:${shiftDate(event.date, 1).replaceAll('-', '')}`,
      `SUMMARY:${calendarText(`${event.name}｜${opportunity.title}`)}`, 'END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return `${lines.map(foldLine).join('\r\n')}\r\n`;
}
