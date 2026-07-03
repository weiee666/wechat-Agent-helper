import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vercel 部署：outputDirectory=web/dashboard-react/dist
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 5173,
    proxy: {
      // 本地 dev 时把 /api 转发到 vercel-cli / 手写代理
      '/api': 'http://localhost:3000',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
})
