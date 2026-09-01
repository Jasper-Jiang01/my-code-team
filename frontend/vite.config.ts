import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 部署到 GitHub Pages 项目页面（https://<user>.github.io/my-code-team/）时，
// 静态资源需要以仓库名作为子路径前缀；本地开发 / 其他平台（如 Vercel）保持根路径 '/'。
export default defineConfig({
  base: process.env.GITHUB_PAGES ? '/my-code-team/' : '/',
  plugins: [react()],
  server: {
    port: 5173,
  },
});
