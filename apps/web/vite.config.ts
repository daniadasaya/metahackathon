import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The only place in the web app where a port number appears.
// 8100 is the bare `uvicorn` dev server; the app itself always calls
// relative paths (/api, /p) so it never learns an origin or a port.
const API_DEV_TARGET = 'http://127.0.0.1:8100';

export default defineConfig({
  // Absolute asset paths. './' breaks a deep-link refresh because
  // './assets/x.js' on /review resolves to /review/assets/x.js and 404s.
  base: '/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 4100,
    host: '127.0.0.1',
    proxy: {
      '/api': { target: API_DEV_TARGET },
      '/p': { target: API_DEV_TARGET },
    },
  },
  preview: {
    port: 4180,
    host: '127.0.0.1',
  },
});
