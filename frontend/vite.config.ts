import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Talos 注入 PUBLIC_PATH；GitHub Pages 仍可用 GITHUB_PAGES 子路径。
// 本地 dev 把 /api 代理到 FastAPI，这样 VITE_API_BASE_URL 留空即可走同域。
export default defineConfig({
  base: process.env.PUBLIC_PATH || (process.env.GITHUB_PAGES ? '/my-code-team/' : '/'),
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: { outDir: 'build' },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
