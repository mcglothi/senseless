import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: `npm run dev` proxies /api to a running Senseless (SENSELESS_API, default localhost:8780).
const API = process.env.SENSELESS_API || 'http://127.0.0.1:8780'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: API, changeOrigin: true } },
  },
  build: { outDir: 'dist' },
  test: { environment: 'node' },
})
