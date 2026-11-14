import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// In dev, /api/* is proxied to the FastAPI backend (make backend-dev), so no CORS is involved.
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: process.env.API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
