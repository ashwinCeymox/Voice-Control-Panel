import { defineConfig, Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';

function vadStaticPlugin(): Plugin {
  return {
    name: 'vad-static-plugin',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ? req.url.split('?')[0] : '';
        if (url.startsWith('/vad/')) {
          const filePath = path.join(__dirname, 'public', url);
          if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
            if (filePath.endsWith('.mjs') || filePath.endsWith('.js')) {
              res.setHeader('Content-Type', 'application/javascript');
            } else if (filePath.endsWith('.wasm')) {
              res.setHeader('Content-Type', 'application/wasm');
            } else if (filePath.endsWith('.onnx')) {
              res.setHeader('Content-Type', 'application/octet-stream');
            }
            res.setHeader('Access-Control-Allow-Origin', '*');
            res.setHeader('Cross-Origin-Resource-Policy', 'cross-origin');
            fs.createReadStream(filePath).pipe(res);
            return;
          }
        }
        next();
      });
    },
    resolveId(id) {
      if (id.includes('/vad/ort-wasm-simd-threaded.mjs')) {
        return path.join(__dirname, 'public/vad/ort-wasm-simd-threaded.mjs');
      }
      return null;
    },
    load(id) {
      if (id.endsWith('/vad/ort-wasm-simd-threaded.mjs') || id.includes('ort-wasm-simd-threaded.mjs')) {
        const filePath = id.startsWith('/') && fs.existsSync(id)
          ? id
          : path.join(__dirname, 'public/vad/ort-wasm-simd-threaded.mjs');
        if (fs.existsSync(filePath)) {
          return fs.readFileSync(filePath, 'utf-8');
        }
      }
      return null;
    },
  };
}

export default defineConfig({
  base: '/voicecontrolpanel/',
  plugins: [react(), vadStaticPlugin()],
  assetsInclude: ['**/*.wasm', '**/*.onnx'],
  build: {
    sourcemap: false,
  },
  server: {
    port: 5173,
    host: '127.0.0.1',
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'credentialless',
    },
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
