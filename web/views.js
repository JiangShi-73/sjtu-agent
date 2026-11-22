import { emptyProfile, opportunityStatus, taskIssue } from './domain.js';
import { abilityPanel, experienceLevels } from './ability.js';
import { collegeNames, curriculumUrl, customCollege, customMajor, majorsFor } from './academic.js';
import { themeButton } from './theme.js';

export const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);
const h = escapeHtml;

export const checkLabels = {
  met: '已满足', unknown: '待核实', unmet: '不满足', related: '已有相关经历', improve: '可补充',
};
const opportunityLabels = { open: '报名未截止', undated: '截止日待确认', expired: '已截止' };
const issueLabels = { invalid: '请选择有效日期', late: '晚于申请目标日', past: '已过计划日期，可调整或确认保留' };

export const opportunityPath = (opportunity, suffix = '') => `#/opportunities/${encodeURIComponent(opportunity.id)}${suffix}`;
const dateText = (date) => date ? h(date.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$1 年 $2 月 $3 日')) : '待确认';
const badge = (status, labels = checkLabels) => `<span class="badge ${status}">${labels[status]}</span>`;
export const buttonLink = (href, text, secondary = false) =>
  `<a class="button ${secondary ? 'secondary' : 'primary'}" href="${h(href)}">${h(text)}</a>`;
export function guestGuide(repo) {
  if (repo.auth.user) return '';
  return `<aside class="guest-guide" aria-label="访客引导"><div><strong>配置你的校园日程</strong><p>注册后可保存学院、专业和个人待办，在日程中查看本学院通知。</p></div>${buttonLink('#/register', '注册并填写资料')}</aside>`;
}
const sectionTitle = (title, sub = '') => `<div class="section-title"><h2>${h(title)}</h2>${sub ? `<p>${h(sub)}</p>` : ''}</div>`;

export function shell(content, active, repo, demo) {
  const links = [['schedule', '日程导视'], ['opportunities', '机会探索'], ['profile', '个人背景'], ['plans', '申请计划']];
  const theme = themeButton();
  const user = repo.auth.user;
  const accountLabel = user ? `${user.name}的账号` : '登录或注册';
  return `<div class="layout">
    <aside class="rail">
      <a class="brand" href="#/schedule"><span class="brand-mark" aria-hidden="true"><i></i></span><span>PathFinder <small>SJTU</small></span></a>
      <nav aria-label="主导航">${links.map(([key, label]) => `<a href="#/${key}" ${active === key ? 'aria-current="page"' : ''}><i class="nav-icon ${key}" aria-hidden="true"></i>${label}</a>`).join('')}</nav>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <span>${demo ? `演示 · ${h(repo.today())}` : '交我成长导航'}</span>
        <div class="account-tools">
          <span id="sync-status" role="status"></span>
          <button type="button" class="text-link" id="retry-sync" hidden>重试保存</button>
          <button type="button" class="icon-button theme-toggle" id="theme-toggle" aria-label="夜间模式" aria-pressed="${theme.pressed}" title="${theme.title}">
            <span class="toolbar-icon moon-icon" aria-hidden="true"></span>
            <span class="toolbar-icon sun-icon" aria-hidden="true"></span>
          </button>
          <a class="icon-button account-entry" href="#/login" aria-label="${h(accountLabel)}" title="${h(accountLabel)}" ${user ? 'data-signed-in' : ''}>
            <span class="toolbar-icon account-icon" aria-hidden="true"></span>
          </a>
        </div>
      </header>
      <main id="main-content" tabindex="-1">${content}</main>
    </div>
  </div>`;
}

export function pageHead(title, note, backHref = '', backLabel = '') {
  return `<header class="page-head">
    ${backHref ? `<a class="back-link" href="${h(backHref)}">${h(backLabel)}</a>` : ''}
    <h1>${h(title)}</h1>${note ? `<p>${h(note)}</p>` : ''}
  </header>`;
}

function opportunityTile(opportunity, repo, selected) {
  const isCamp = opportunity.type === 'summer-camp';
  return `<a class="opportunity-tile ${selected ? 'selected' : ''}" href="${opportunityPath(opportunity)}" ${selected ? 'aria-current="page"' : ''}>
    <span class="category-icon ${isCamp ? 'camp' : 'job'}" aria-hidden="true">${isCamp ? '学' : '职'}</span>
    <div class="tile-body"><div class="tile-heading"><h3>${h(opportunity.title)}</h3>${badge(opportunityStatus(opportunity, repo.today()), opportunityLabels)}</div>
    <p>${h(opportunity.publisher)} · ${h(repo.options.opportunityType[opportunity.type])}</p>
    <p class="tile-date">${opportunity.deadlineDate ? `截止 ${dateText(opportunity.deadlineDate)}` : '暂无公告截止日'}</p></div>
  </a>`;
}

export function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' || url.protocol === 'http:') return url.href;
  } catch {
    return '';
  }
  return '';
}

function externalLink(value, label) {
  const url = safeExternalUrl(value);
  return url ? `<a class="text-link" target="_blank" rel="noopener noreferrer" href="${h(url)}">${h(label)}</a>` : '';
}

function opportunityDetail(opportunity, repo, selected) {
  const status = opportunityStatus(opportunity, repo.today());
  const { source, materials } = opportunity;
  const heading = selected ? 'h1' : 'h2';
  return `<article class="panel detail" id="opportunity-detail">
    <a class="mobile-back back-link" href="#/opportunities">返回机会列表</a>
    <div class="detail-top"><span class="category-icon ${opportunity.type === 'summer-camp' ? 'camp' : 'job'}" aria-hidden="true">${opportunity.type === 'summer-camp' ? '学' : '职'}</span>${source.kind === 'demo' ? '<span class="demo-tag">虚构样例</span>' : ''}</div>
    <${heading}>${h(opportunity.title)}</${heading}><p class="detail-publisher">${h(opportunity.publisher)} · ${h(repo.options.opportunityType[opportunity.type])}</p>
    <dl class="metadata"><div><dt>发布日</dt><dd>${dateText(opportunity.publishedDate)}</dd></div><div><dt>截止日</dt><dd>${dateText(opportunity.deadlineDate)}</dd></div></dl>
    <section class="source-block" id="notice"><h3>公告摘要</h3><blockquote>${h(source.excerpt)}</blockquote>
      <div class="opportunity-source-links">${externalLink(source.url, '查看公告原文')}${status !== 'expired' ? externalLink(opportunity.applicationUrl, '进入官方投递') : ''}</div>
    </section>
    <section class="materials"><h3>申请材料</h3>${materials.length ? `<ul>${materials.map(({ name }) => `<li>${h(name)}</li>`).join('')}</ul>` : '<p>材料要求请查看公告或岗位页面。</p>'}</section>
    ${status === 'expired' ? '<p class="inline-notice">此机会已截止，仍可查看和核验条件。</p>' : ''}
    ${buttonLink(opportunityPath(opportunity, '/check'), '核验我的条件')}
  </article>`;
}

export function opportunitiesView(repo, filter, selected) {
  const list = repo.opportunities.filter(({ type }) => filter === 'all' || type === filter);
  const current = selected ?? list[0];
  const filters = [['all', '全部机会'], ...Object.entries(repo.options.opportunityType)];
  const heading = selected ? 'p' : 'h1';
  return `<div class="opportunity-page ${selected ? 'detail-open' : ''}">
    <header class="hero">
      <div class="hero-copy"><${heading} class="hero-title">升学与求职机会</${heading}></div>
    </header>
    <div class="opportunity-grid">
      <section class="opportunity-list" aria-labelledby="list-title">
        <div class="list-heading"><h2 id="list-title">机会列表</h2><span>${list.length} 项机会</span></div>
        <div class="filters" aria-label="机会类别">${filters.map(([value, name]) => `<button type="button" data-filter="${h(value)}" aria-pressed="${filter === value}">${h(name)}</button>`).join('')}</div>
        <div class="tiles">${list.length ? list.map((item) => opportunityTile(item, repo, item === current)).join('') : '<p class="panel empty">此类别暂时没有机会。<br><button type="button" class="text-link" data-filter="all">查看全部机会</button></p>'}</div>
        <p class="list-foot">${repo.reviewedOn ? `公开公告整理于 ${dateText(repo.reviewedOn)}` : '当前为演示数据。'}</p>
      </section>
      ${current ? opportunityDetail(current, repo, Boolean(selected)) : ''}
    </div>
  </div>`;
}

const optionsMarkup = (options, value) => '<option value="">暂不填写</option>' + Object.entries(options)
  .map(([id, label]) => `<option value="${h(id)}" ${id === value ? 'selected' : ''}>${h(label)}</option>`).join('');

export function majorOptions(collegeId, majorId) {
  if (!collegeId) return '<option value="">请先选择学院</option>';
  return optionsMarkup({ ...majorsFor(collegeId), [customMajor]: '其他专业（手动填写）' }, majorId);
}

export function profileView(repo, returnOpportunity, cleared = false) {
  const profile = repo.getProfile() ?? emptyProfile;
  const exampleOptions = repo.examples.map(({ id, label }) => `<option value="${h(id)}">${h(label)}</option>`).join('');
  return `${pageHead('个人背景', '', returnOpportunity ? opportunityPath(returnOpportunity, '/check') : '#/schedule', returnOpportunity ? '返回条件核验' : '返回日程导视')}
    ${abilityPanel('ability-profile')}
    ${guestGuide(repo)}
    <div class="form-grid">
      <form class="panel content-panel" id="profile-form">
        <div class="dash-card-head"><h2>基本信息</h2>${externalLink(curriculumUrl, '交大培养计划')}</div>
        <div class="form-fields">
          <label>学院<select name="collegeId">${optionsMarkup({ ...collegeNames, [customCollege]: '其他院系（手动填写）' }, profile.collegeId)}</select></label>
          <label>专业<select name="majorId" ${profile.collegeId ? '' : 'disabled'}>${majorOptions(profile.collegeId, profile.majorId)}</select></label>
          <label id="custom-college" ${profile.collegeId === customCollege ? '' : 'hidden'}>学院名称<input name="collegeText" maxlength="60" value="${h(profile.collegeText)}"></label>
          <label id="custom-major" ${profile.majorId === customMajor ? '' : 'hidden'}>专业名称<input name="majorText" maxlength="60" value="${h(profile.majorText)}"></label>
          <label>在读年级<select name="studyYear">${optionsMarkup(repo.options.studyYear, profile.studyYear)}</select></label>
          <label>预计毕业月份<input name="graduationMonth" type="month" min="1900-01" max="2099-12" value="${h(profile.graduationMonth)}" placeholder="YYYY-MM" pattern="[0-9]{4}-(0[1-9]|1[0-2])"></label>
          <label>专业排名前（%）<input name="rankPercent" type="number" min="0" max="100" step="any" inputmode="decimal" value="${h(profile.rankPercent)}" placeholder="例如 12"></label>
          <label>CET-6 成绩<input name="cet6Score" type="number" min="0" max="710" step="1" inputmode="numeric" value="${h(profile.cet6Score)}" placeholder="例如 425"></label>
        </div>
        <fieldset class="experience"><legend>相关经历</legend>
          <div class="chips">${Object.entries(repo.options.experienceTags).map(([id, label]) => `<label><input type="checkbox" name="experienceTags" value="${h(id)}" ${profile.experienceTags?.includes(id) ? 'checked' : ''}><span>${h(label)}</span></label>`).join('')}</div>
          <label class="check-line"><input type="checkbox" name="noExperience" ${profile.experienceTags?.length === 0 ? 'checked' : ''}>确认暂无相关经历</label>
          <div class="form-fields experience-levels">${Object.entries(repo.options.experienceTags).map(([id, label]) => {
            const selected = profile.experienceTags?.includes(id);
            return `<label data-experience-level="${h(id)}" ${selected ? '' : 'hidden'}>${h(label)}参与程度<select name="depth-${h(id)}" ${selected ? '' : 'disabled'}>${optionsMarkup(experienceLevels, String(profile.experienceDepth?.[id] ?? '')).replace('暂不填写', '仅登记经历')}</select></label>`;
          }).join('')}</div>
        </fieldset>
        <div class="form-actions"><button class="button secondary" type="button" id="clear-profile">清空背景</button><button class="button primary" type="submit">${returnOpportunity ? '保存并返回核验' : '保存背景'}</button></div>
        <p class="inline-notice" id="profile-save-status" role="status">${cleared ? '个人背景已清空。' : ''}</p>
      </form>
      <aside class="panel side-note"><h2>示例背景</h2><label class="sr-only" for="sample-profile">选择示例</label><select id="sample-profile">${exampleOptions}</select><button class="button secondary" id="fill-profile" type="button">填入示例</button><p class="privacy-note">${repo.auth.user ? '已登录，资料保存至个人账号。' : repo.persistent ? '访客资料保存在此浏览器。' : '当前浏览器无法保存资料。'}</p></aside>
    </div>`;
}

function profileValue(value, field, options) {
  if (value === null) return '未填写';
  if (field === 'rankPercent') return `前 ${value}%`;
  if (field === 'cet6Score') return `${value} 分`;
  if (field === 'graduationMonth') return value.replace(/^(\d{4})-(\d{2})$/, '$1 年 $2 月');
  if (field === 'experienceTags') return value.length ? value.map((tag) => options[field][tag] ?? tag).join('、') : '暂无相关经历';
  return options[field]?.[value] ?? value;
}

export function checkView(repo, opportunity) {
  const result = repo.assess(opportunity);
  const profile = repo.getProfile();
  const academicContext = [collegeNames[profile?.collegeId] ?? profile?.collegeText,
    majorsFor(profile?.collegeId)[profile?.majorId] ?? profile?.majorText].filter(Boolean).join(' · ');
  const hard = result.items.filter((_, i) => opportunity.requirements[i].kind === 'hard');
  const expired = opportunityStatus(opportunity, repo.today()) === 'expired';
  const renderRequirements = (kind) => opportunity.requirements.map((requirement, index) => {
    if (requirement.kind !== kind) return '';
    const item = result.items[index];
    const userValue = requirement.operator === 'manual' ? '需自行核对' : profileValue(item.userValue, requirement.field, repo.options);
    return `<article class="rule-item"><div class="rule-heading"><h3>${h(requirement.label)}</h3>${badge(item.status)}</div>
      <p class="rule-evidence">${h(requirement.evidence)}</p><p><strong>你的情况</strong> ${h(userValue)}</p><p class="rule-explanation">${h(item.explanation)}</p></article>`;
  }).join('');
  return `${pageHead('条件核验', opportunity.title, opportunityPath(opportunity), '返回机会详情')}
    <div class="assessment-top panel"><div><h2>硬性条件</h2>${academicContext ? `<p>${h(academicContext)}</p>` : ''}<p>${hard.length ? ['met', 'unknown', 'unmet'].map((status) => `${checkLabels[status]} ${hard.filter((item) => item.status === status).length} 项`).join(' · ') : '暂无可逐项核验的条件。'}</p></div>${badge(result.summary)}</div>
    <section class="rules" aria-label="硬性条件详情">${renderRequirements('hard')}</section>
    <section class="soft-rules">${sectionTitle('软性偏好', '这些偏好可以帮助你准备材料。')}<div class="rules">${renderRequirements('soft') || '<p class="panel empty">公告未列出软性偏好。</p>'}</div></section>
    <div class="action-strip panel"><div>${expired ? '<p class="inline-notice">此机会已截止，无法生成计划。</p>' : result.summary === 'unmet' ? '<p class="inline-notice">存在硬性差距，可先用计划梳理准备事项。</p>' : ''}</div><div class="strip-buttons">${buttonLink(`#/profile?return=${encodeURIComponent(opportunity.id)}`, '补充个人背景', true)}${expired ? '<button class="button primary" disabled>生成申请计划</button>' : buttonLink(opportunityPath(opportunity, '/plan'), '生成申请计划')}</div></div>
    ${abilityPanel('ability-check')}`;
}

export function planView(repo, opportunity, plan) {
  const head = pageHead('申请计划', opportunity.title, opportunityPath(opportunity, '/check'), '返回条件核验');
  const today = repo.today();
  if (opportunityStatus(opportunity, today) === 'expired') return `${head}<section class="panel content-panel"><h2>该机会已截止</h2><p>可返回机会详情查看原公告和申请要求。</p>${buttonLink(opportunityPath(opportunity), '查看机会详情', true)}</section>`;
  if (!plan) return `${head}<form class="panel content-panel target-start" id="target-form"><h2>设定申请目标日</h2><p>公告暂未提供截止日。选一个日期，用来倒推准备任务。</p><label>自己的目标日<input name="targetDate" type="date" min="${h(today)}" max="9999-12-30" required></label><button class="button primary" type="submit">生成准备任务</button></form>`;
  const tasks = plan.tasks.map((task, index) => `<div class="task-row" data-task="${h(task.id)}">
    <div><h3>${h(task.name)}</h3><p data-task-note>${task.manual ? '手动调整' : '按目标日倒推'}</p></div>
    <label for="lead-${index}">提前天数<input id="lead-${index}" data-field="leadDays" type="number" min="0" max="3650" step="1" value="${h(task.leadDays)}" required></label>
    <label for="date-${index}">计划日期<input id="date-${index}" data-field="date" type="date" max="9999-12-30" value="${h(task.date)}" required aria-describedby="issue-${index}"><span class="task-issue" id="issue-${index}">${issueLabels[taskIssue(task, plan.targetDate, today)] ?? ''}</span></label>
  </div>`).join('');
  return `${head}<form class="panel content-panel plan-form" id="plan-form">
    <div class="plan-target"><div><h2>申请目标日</h2><p>${plan.targetDateSource === 'official' ? '来自公告' : '自己设定'}</p></div>${plan.targetDateSource === 'official' ? `<strong>${dateText(plan.targetDate)}</strong>` : `<input aria-label="申请目标日" name="targetDate" type="date" min="${h(today)}" max="9999-12-30" value="${h(plan.targetDate)}" required>`}</div>
    ${sectionTitle('准备任务', '提前天数是建议值，按你的办理进度调整。')}
    <div class="task-list">${tasks}<div class="task-row task-submit"><h3>核对并提交申请</h3><span></span><strong id="submission-date">${dateText(plan.targetDate)}</strong></div></div>
    <p class="inline-notice" id="plan-message" role="status"></p>
    <div class="form-actions"><button class="button primary" type="submit">保存并确认计划</button><button class="button secondary" id="download-plan" type="button" disabled>下载 .ics</button></div>
  </form>`;
}

export function paintPlan(form, plan, repo, confirmable, edited, invalid) {
  for (const task of plan.tasks) {
    const row = [...form.querySelectorAll('[data-task]')].find((item) => item.dataset.task === task.id);
    const date = row.querySelector('[data-field="date"]');
    if (date !== edited && !invalid.has(date)) date.value = task.date;
    row.querySelector('[data-task-note]').textContent = task.manual ? '手动调整' : '按目标日倒推';
    const issue = taskIssue(task, plan.targetDate, repo.today());
    const rowInvalid = [...row.querySelectorAll('input')].some((input) => invalid.has(input));
    row.querySelector('.task-issue').textContent = rowInvalid ? '请输入有效的日期和提前天数' : issueLabels[issue] ?? '';
  }
  form.querySelector('#submission-date').textContent = dateText(plan.targetDate);
  form.querySelector('button[type="submit"]').disabled = !confirmable || plan.confirmed;
  form.querySelector('#download-plan').disabled = !confirmable || !plan.confirmed;
  let message = '修改任务或目标日后，请重新确认。';
  if (invalid.size) message = '请先修正无效的输入。';
  else if (!confirmable) message = '请修正任务日期或申请目标日后再确认。';
  else if (plan.confirmed) message = '计划已确认，可以下载日历。';
  else if (!repo.persistent) message = '当前浏览器无法保存，建议确认后立即下载。';
  form.querySelector('#plan-message').textContent = message;
}

export function plansView(repo) {
  const entries = repo.getPlans().map((plan) => {
    const opportunity = repo.getOpportunity(plan.opportunityId);
    if (!opportunity) return '';
    const expired = opportunityStatus(opportunity, repo.today()) === 'expired';
    return `<article class="panel saved-plan"><div><h2>${h(opportunity.title)}</h2><p>目标日 ${dateText(plan.targetDate)} · ${expired ? '机会已截止' : plan.confirmed ? '已确认' : '待确认'}</p></div><div class="strip-buttons"><button class="button secondary" type="button" data-delete="${h(opportunity.id)}">删除</button>${buttonLink(opportunityPath(opportunity, '/plan'), '查看计划')}</div></article>`;
  }).join('');
  return `${pageHead('申请计划')}${entries || `<section class="panel content-panel"><h2>还没有申请计划</h2>${buttonLink('#/opportunities', '浏览机会')}</section>`}`;
}
