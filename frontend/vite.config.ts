import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendOrigin = process.env.APP_URL || process.env.BACKEND_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT) || 5173,
    strictPort: !process.env.PORT,
    proxy: {
      '/api': { target: backendOrigin, timeout: 120000, proxyTimeout: 120000 },
      '/health': backendOrigin,
      '/legacy': backendOrigin,
      '/static': backendOrigin,
    },
  },
});
