import { DatabaseSync } from 'node:sqlite';
import { createHash, randomBytes, randomUUID, scrypt, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';
import { mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createAssistant } from './assistant.mjs';

const derive = promisify(scrypt);
const hash = (value) => createHash('sha256').update(value).digest('hex');
const generate = () => randomBytes(32).toString('base64url');
const day = 86400;
const fail = (status, message) => Object.assign(new Error(message), { status });

async function body(req) {
  if (req.headers['content-type']?.split(';')[0] !== 'application/json') throw fail(415, '请使用 JSON 请求。');
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 131072) throw fail(413, '提交内容过长。');
    chunks.push(chunk);
  }
  try {
    const data = JSON.parse(Buffer.concat(chunks).toString());
    if (data && typeof data === 'object' && !Array.isArray(data)) return data;
  } catch {}
  throw fail(400, '请求格式无效。');
}

export function createAuth(origin) {
  const secure = origin.startsWith('https://');
  if (!secure && !['localhost', '127.0.0.1', '[::1]'].includes(new URL(origin).hostname)) {
    throw new Error('请将 PATHFINDER_ORIGIN 设为站点的 HTTPS 地址。');
  }
  const dir = resolve(process.env.PATHFINDER_DATA_DIR || '.pathfinder-data');
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const db = new DatabaseSync(join(dir, 'accounts.sqlite'));
  db.exec(`PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, login TEXT UNIQUE NOT NULL, name TEXT NOT NULL, salt TEXT, digest TEXT);
    CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user TEXT NOT NULL, expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS states (token TEXT PRIMARY KEY, expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS records (user TEXT, mode TEXT, kind TEXT, id TEXT, value TEXT NOT NULL, PRIMARY KEY(user,mode,kind,id));`);
  const clientId = process.env.JACCOUNT_CLIENT_ID;
  const secret = process.env.JACCOUNT_CLIENT_SECRET;
  const campus = Boolean(clientId && secret);
  const callback = origin + '/api/jaccount/callback';
  const assistant = createAssistant();
  const listRecords = db.prepare('SELECT kind,id,value FROM records WHERE user=? AND mode=?');
  const getRecord = db.prepare('SELECT value FROM records WHERE user=? AND mode=? AND kind=? AND id=?');
  const insertRecord = db.prepare('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(user,mode,kind,id) DO NOTHING');
  function readData(userId, mode) {
    const data = { profile: null, plans: Object.create(null), todos: [] };
    for (const row of listRecords.all(userId, mode)) {
      const value = JSON.parse(row.value);
      if (row.kind === 'profile') data.profile = value;
      if (row.kind === 'plan') data.plans[row.id] = value;
      if (row.kind === 'todo') data.todos.push(value);
    }
    return data;
  }
  const attempts = new Map();
  let working = 0;
  const prune = () => {
    db.prepare('DELETE FROM sessions WHERE expires<=?').run(Date.now());
    db.prepare('DELETE FROM states WHERE expires<=?').run(Date.now());
    for (const [key, entry] of attempts) if (entry.end <= Date.now()) attempts.delete(key);
  };
  prune();
  setInterval(prune, 600000).unref();
  function limit(key, max) {
    let entry = attempts.get(key);
    if (!entry || entry.end <= Date.now()) {
      entry = { count: 0, end: Date.now() + 600000 };
      attempts.set(key, entry);
    }
    if (++entry.count > max) throw fail(429, '请求过于频繁，请稍后再试。');
  }
  const cookieName = (name) => (secure ? '__Host-' : '') + 'pf_' + name;
  const cookie = (name, value, age) => `${cookieName(name)}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${age}${secure ? '; Secure' : ''}`;

  return async (req, res, url) => {
    const { pathname: path } = url;
    const cookies = Object.fromEntries((req.headers.cookie || '').split(';').map((part) => part.trim().split('=')));
    const token = hash(cookies[cookieName('session')] || '');
    const state = cookies[cookieName('state')];
    const json = (status, value) => res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }).end(JSON.stringify(value));
    function login(id) {
      const next = generate();
      db.prepare('DELETE FROM sessions WHERE token=?').run(token);
      db.prepare('INSERT INTO sessions VALUES(?,?,?)').run(hash(next), id, Date.now() + 7 * day * 1000);
      res.setHeader('Set-Cookie', [cookie('session', next, 7 * day), cookie('state', '', 0)]);
    }
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Referrer-Policy', 'no-referrer');
    try {
      if (req.headers.host !== new URL(origin).host) throw fail(403, '站点地址不匹配。');
      if (req.method === 'POST' && req.headers.origin !== origin) throw fail(403, '请从本站页面提交。');
      const user = db.prepare('SELECT u.id,u.name FROM users u JOIN sessions s ON u.id=s.user WHERE s.token=? AND s.expires>?').get(token, Date.now()) || null;
      if (path === '/api/session' && req.method === 'GET') {
        return json(200, { user, campus, ...(user && assistant.models.length ? { assistantModels: assistant.models } : {}) });
      }
      const ip = req.socket.remoteAddress + ':' + (secure ? req.headers['x-real-ip'] || '' : '');
      if (path === '/api/jaccount/start' && req.method === 'GET' && campus) {
        limit('campus:' + ip, 60);
        const nonce = generate();
        db.prepare('INSERT INTO states VALUES(?,?)').run(hash(nonce), Date.now() + 600000);
        const params = new URLSearchParams({ response_type: 'code', client_id: clientId, scope: 'basic', redirect_uri: callback, state: nonce });
        return res.writeHead(302, { 'Set-Cookie': cookie('state', nonce, 600), Location: 'https://jaccount.sjtu.edu.cn/oauth2/authorize?' + params }).end();
      }
      if (path === '/api/jaccount/callback' && req.method === 'GET' && campus) {
        if (!state || state !== url.searchParams.get('state') ||
            !db.prepare('DELETE FROM states WHERE token=? AND expires>?').run(hash(state), Date.now()).changes ||
            !url.searchParams.get('code')) throw fail(400, '校园授权失效。');
        const reply = await fetch('https://jaccount.sjtu.edu.cn/oauth2/token', {
          method: 'POST', redirect: 'error', signal: AbortSignal.timeout(8000),
          body: new URLSearchParams({ grant_type: 'authorization_code', code: url.searchParams.get('code'), redirect_uri: callback, client_id: clientId, client_secret: secret }),
        });
        if (!reply.ok) throw fail(502, '校园授权失败。');
        const { access_token: access } = await reply.json();
        if (typeof access !== 'string' || !access) throw fail(502, '校园授权失败。');
        const result = await fetch('https://api.sjtu.edu.cn/v1/me/profile', {
          headers: { Authorization: 'Bearer ' + access }, redirect: 'error', signal: AbortSignal.timeout(8000),
        });
        if (!result.ok) throw fail(502, '校园身份读取失败。');
        const profile = await result.json();
        const person = profile.entities?.[0];
        if (profile.errno !== 0 || typeof person?.id !== 'string' || !person.id) throw fail(403, '请使用个人 jAccount。');
        const key = 'sjtu:' + person.id;
        const account = db.prepare('INSERT INTO users(id,login,name) VALUES(?,?,?) ON CONFLICT(login) DO UPDATE SET name=excluded.name RETURNING id')
          .get(randomUUID(), key, person.name || '交大用户');
        login(account.id);
        return res.writeHead(303, { Location: '/#/schedule' }).end();
      }
      if (path === '/api/logout' && req.method === 'POST') {
        db.prepare('DELETE FROM sessions WHERE token=?').run(token);
        res.setHeader('Set-Cookie', [cookie('session', '', 0), cookie('state', '', 0)]);
        return json(200, { ok: true });
      }
      if (['/api/register', '/api/login'].includes(path) && req.method === 'POST') {
        const { username, password } = await body(req);
        if (typeof username !== 'string' || !/^[a-zA-Z0-9_.-]{3,32}$/.test(username) ||
            typeof password !== 'string' || password.length < 10 || password.length > 128) throw fail(400, '用户名需为 3–32 位字母、数字或 _.-，密码需为 10–128 位。');
        const key = 'local:' + username.toLowerCase();
        limit(ip, 80);
        limit(path + key, 12);
        if (working >= 4) throw fail(429, '服务繁忙，请稍后再试。');
        working++;
        try {
          const register = path === '/api/register';
          const found = db.prepare('SELECT * FROM users WHERE login=?').get(key);
          const salt = register || !found ? generate() : found.salt;
          const digest = await derive(password, salt, 64, { N: 32768, maxmem: 64 * 1024 * 1024 });
          if (register) {
            const id = randomUUID();
            const inserted = db.prepare('INSERT INTO users VALUES(?,?,?,?,?) ON CONFLICT(login) DO NOTHING').run(id, key, username, salt, digest.toString('hex'));
            if (!inserted.changes) throw fail(409, '该用户名已被使用。');
            login(id);
          } else {
            if (!found || !timingSafeEqual(digest, Buffer.from(found.digest, 'hex'))) throw fail(401, '用户名或密码有误。');
            login(found.id);
          }
          return json(200, { ok: true });
        } finally { working--; }
      }
      if (path === '/api/data' || path === '/api/assistant' || path === '/api/assistant/apply') {
        if (!user || req.headers['x-pathfinder-user'] !== user.id) throw fail(401, '登录状态已变更，请刷新页面。');
        const mode = url.searchParams.get('demo') === '1' ? 'demo' : 'local';
        if (path === '/api/assistant' && req.method === 'POST') {
          return json(200, await assistant.ask(user.id, mode, readData(user.id, mode), await body(req)));
        }
        if (path === '/api/assistant/apply' && req.method === 'POST') {
          if (!assistant.models.length) throw fail(503, '智能体尚未配置。');
          const { actions } = await body(req);
          const records = await assistant.records(mode, actions);
          db.exec('BEGIN IMMEDIATE');
          try {
            const created = records.map(({ kind, id, value }) => {
              const result = insertRecord.run(user.id, mode, kind, id, JSON.stringify(value));
              return { kind, value: result.changes ? value : JSON.parse(getRecord.get(user.id, mode, kind, id).value) };
            });
            db.exec('COMMIT');
            return json(200, { created });
          } catch (error) {
            db.exec('ROLLBACK');
            throw error;
          }
        }
        if (path === '/api/data' && req.method === 'GET') return json(200, readData(user.id, mode));
        if (path === '/api/data' && req.method === 'POST') {
          const { kind, id, value } = await body(req);
          if (!['profile','plan','todo'].includes(kind) || typeof id !== 'string' || !/^[\w-]{1,100}$/.test(id) ||
              (kind === 'profile' && id !== 'me') || (value !== null && (!value || typeof value !== 'object' || Array.isArray(value)))) throw fail(400, '记录格式无效。');
          if (value === null) db.prepare('DELETE FROM records WHERE user=? AND mode=? AND kind=? AND id=?').run(user.id,mode,kind,id);
          else db.prepare('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(user,mode,kind,id) DO UPDATE SET value=excluded.value').run(user.id,mode,kind,id,JSON.stringify(value));
          return json(200, { ok: true });
        }
      }
      return json(404, { error: '接口不可用。' });
    } catch (error) {
      if (path === '/api/jaccount/callback') return res.writeHead(303, { 'Set-Cookie': cookie('state','',0), Location: '/#/login?error=campus' }).end();
      return json(error.status || 500, { error: error.status ? error.message : '服务暂时不可用。' });
    }
  };
}
