import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('.', import.meta.url))
const headers = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Content-Security-Policy': "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
  // Observe compatibility with Vite refresh and existing style attributes before enforcement.
  'Content-Security-Policy-Report-Only': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: http://127.0.0.1:8000; connect-src 'self' http://127.0.0.1:8000 ws://127.0.0.1:5173 ws://localhost:5173; frame-src 'none'; object-src 'none'; base-uri 'none'",
}
export default defineConfig({
  plugins: [react(), tailwindcss()],
  envDir: root,
  envPrefix: 'VITE_PUBLIC_',
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    allowedHosts: ['localhost', '127.0.0.1'],
    cors: { origin: ['http://127.0.0.1:5173', 'http://localhost:5173'] },
    fs: { strict: true, allow: [root, fileURLToPath(new URL('../../node_modules', import.meta.url))], deny: ['**/.env', '**/.env.*', '**/.git/**', '**/*.{pem,key}'] },
    headers,
  },
  preview: { host: '127.0.0.1', port: 5173, strictPort: true, allowedHosts: ['localhost', '127.0.0.1'], headers },
})
