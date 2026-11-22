import { copyFile, mkdir, readdir } from 'node:fs/promises';

const source = new URL('../web/', import.meta.url);
const output = new URL('../dist/', import.meta.url);
await mkdir(output, { recursive: true });
for (const entry of await readdir(source, { withFileTypes: true })) {
  if (entry.isFile() && /\.(html|css|js)$/.test(entry.name)) {
    await copyFile(new URL(entry.name, source), new URL(entry.name, output));
  }
}
await copyFile(new URL('../fixtures/mvp-demo.json', import.meta.url), new URL('mvp-demo.json', output));
console.log('Web output: dist/');
