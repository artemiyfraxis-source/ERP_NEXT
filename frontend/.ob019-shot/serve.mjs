import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
const server = await createServer({ configFile: false, root: fileURLToPath(new URL('../', import.meta.url)), plugins: [react()], server: { host: '127.0.0.1', port: 5197, strictPort: true } });
await server.listen();
console.log('Mock preview http://127.0.0.1:5197/.ob019-shot/index.html');

