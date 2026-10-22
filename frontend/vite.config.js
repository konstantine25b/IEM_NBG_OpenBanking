import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The browser talks to /api; Vite forwards it to the income API (API/service.py, port 9000).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:9000', rewrite: (path) => path.replace(/^\/api/, '') },
    },
  },
})
