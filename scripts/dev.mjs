import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { createAuth } from '../server/auth.mjs';

const root = new URL('../web/', import.meta.url);
const fixture = new URL('../fixtures/mvp-demo.json', import.meta.url);
const types = { html: 'text/html', css: 'text/css', js: 'text/javascript', json: 'application/json' };
const port = Number(process.env.PATHFINDER_PORT ?? 4173);
const origin = new URL(process.env.PATHFINDER_ORIGIN || `http://127.0.0.1:${port}`).origin;
const auth = createAuth(origin);

createServer(async (request, response) => {
  let url;
  try { url = new URL(request.url, origin); }
  catch { return response.writeHead(400).end(); }
  const pathname = url.pathname;
  if (pathname.startsWith('/api/')) return auth(request, response, url);
  const file = pathname === '/' ? 'index.html' : pathname.slice(1);
  if (!/^[a-z0-9-]+\.(html|css|js)$/.test(file) && file !== 'mvp-demo.json') {
    response.writeHead(404).end();
    return;
  }
  try {
    const body = await readFile(file === 'mvp-demo.json' ? fixture : new URL(file, root));
    response.writeHead(200, {
      'Content-Type': `${types[file.split('.').at(-1)]}; charset=utf-8`,
      'Cache-Control': 'no-store',
    }).end(body);
  } catch (error) {
    response.writeHead(error.code === 'ENOENT' ? 404 : 500).end();
  }
}).listen(port, '127.0.0.1', () => console.log(`PathFinder: http://127.0.0.1:${port}/`));
