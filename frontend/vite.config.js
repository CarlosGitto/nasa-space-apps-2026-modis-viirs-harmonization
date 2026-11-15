import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import VueI18nPlugin from '@intlify/unplugin-vue-i18n/vite'

// In dev, /api/* is proxied to the FastAPI backend (make backend-dev), so no CORS is involved.
export default defineConfig({
  plugins: [
    vue(),
    // Precompiles the translations (src/locales/*.json): smaller bundle, no eval at runtime.
    VueI18nPlugin({ include: fileURLToPath(new URL('./src/locales/**', import.meta.url)) }),
  ],
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
