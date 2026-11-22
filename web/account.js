import { escapeHtml as h } from './views.js';

export async function api(path, { method = 'GET', value, user } = {}) {
  const headers = { Accept: 'application/json' };
  if (value !== undefined) headers['Content-Type'] = 'application/json';
  if (user) headers['X-PathFinder-User'] = user;
  const response = await fetch('/api/' + path, {
    method, headers, credentials: 'same-origin', cache: 'no-store',
    body: value === undefined ? undefined : JSON.stringify(value),
  });
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('账号服务尚未连接。');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '账号服务暂时不可用。');
  return data;
}

export async function session() {
  try { return { ...(await api('session')), online: true }; }
  catch { return { user: null, campus: false, online: false }; }
}

export function accountView(repo, register, campusError) {
  if (!repo.auth.online) return '<section class="account-page"><h1>账号服务暂不可用</h1><p>请稍后重新连接。</p><button class="button primary" data-reconnect>重新连接</button></section>';
  if (repo.auth.user) return `<section class="account-page"><h1>${h(repo.auth.user.name)}</h1><p>个人背景、待办和申请计划保存至此账号。</p><button class="button secondary" data-logout>退出登录</button></section>`;
  const title = register ? '创建账号' : '登录 PathFinder';
  return `<section class="account-page"><h1>${title}</h1>
    <form id="account-form"><label>用户名<input name="username" autofocus pattern="[a-zA-Z0-9_.\\-]{3,32}" maxlength="32" autocomplete="username" required></label>
    ${register ? '<p class="field-hint">3–32 位，可用字母、数字、点、下划线和短横线。</p>' : ''}
    <label>密码<input type="password" name="password" autocomplete="${register ? 'new-password' : 'current-password'}" minlength="10" maxlength="128" required></label>
    ${register ? '<p class="field-hint">至少 10 个字符。</p>' : ''}
    <p role="status" id="account-error">${campusError ? '校园授权未完成，请重试。' : ''}</p>
    <button class="button primary" type="submit">${register ? '注册并登录' : '登录'}</button></form>
    ${repo.auth.campus ? '<a class="button secondary campus-login" href="/api/jaccount/start">使用 jAccount 登录</a>' : ''}
    <a class="text-link" href="${register ? '#/login' : '#/register'}">${register ? '已有账号，去登录' : '创建站内账号'}</a>
  </section>`;
}

export function bindAccount(root, repo, register) {
  function changed(destination = '/schedule') {
    try { localStorage.setItem('pathfinder:auth-change', String(Date.now())); } catch {}
    location.hash = destination;
    location.reload();
  }
  root.querySelector('[data-reconnect]')?.addEventListener('click', () => location.reload());
  root.querySelector('[data-logout]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await repo.flush(); await api('logout', { method: 'POST' }); changed(); }
    catch (error) { window.alert(error.message); button.disabled = false; }
  });
  const form = root.querySelector('#account-form');
  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      await api(register ? 'register' : 'login', {
        method: 'POST', value: { username: form.elements.username.value, password: form.elements.password.value },
      });
      changed(register ? '/profile' : '/schedule');
    } catch (error) {
      form.querySelector('#account-error').textContent = error.message;
      button.disabled = false;
    }
  });
}
