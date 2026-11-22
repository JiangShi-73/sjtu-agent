import { createServer } from 'node:http';
import { createAuth } from './auth.mjs';

const origin = new URL(process.env.PATHFINDER_ORIGIN).origin;
if (!origin.startsWith('https://')) throw new Error('账号服务需要配置 HTTPS 站点地址。');
const auth = createAuth(origin);
createServer((request, response) => {
  let url;
  try { url = new URL(request.url, origin); }
  catch { return response.writeHead(400).end(); }
  return auth(request, response, url);
}).listen(Number(process.env.PATHFINDER_PORT || 4173), '127.0.0.1');
