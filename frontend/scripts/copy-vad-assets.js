import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const frontendRoot = path.resolve(__dirname, '..');
const targetDir = path.join(frontendRoot, 'public', 'vad');

const vadWebDist = path.join(frontendRoot, 'node_modules', '@ricky0123', 'vad-web', 'dist');
const ortWebDist = path.join(frontendRoot, 'node_modules', 'onnxruntime-web', 'dist');

if (!fs.existsSync(targetDir)) {
  fs.mkdirSync(targetDir, { recursive: true });
}

let copiedCount = 0;

function copyFile(src, dest) {
  fs.copyFileSync(src, dest);
  copiedCount++;
}

// 1. Copy Silero VAD onnx models and worklet bundles from @ricky0123/vad-web
if (fs.existsSync(vadWebDist)) {
  const vadFiles = fs.readdirSync(vadWebDist);
  for (const file of vadFiles) {
    if (file.endsWith('.onnx') || file.includes('worklet.bundle.min.js')) {
      copyFile(path.join(vadWebDist, file), path.join(targetDir, file));
    }
  }
} else {
  console.warn(`[copy-vad-assets] Warning: ${vadWebDist} not found. Run npm install first.`);
}

// 2. Copy WASM and MJS runtime files from onnxruntime-web
if (fs.existsSync(ortWebDist)) {
  const ortFiles = fs.readdirSync(ortWebDist);
  for (const file of ortFiles) {
    if (file.endsWith('.wasm') || file.endsWith('.mjs')) {
      copyFile(path.join(ortWebDist, file), path.join(targetDir, file));
    }
  }
} else {
  console.warn(`[copy-vad-assets] Warning: ${ortWebDist} not found. Run npm install first.`);
}

console.log(`[copy-vad-assets] Successfully copied ${copiedCount} VAD and ONNX runtime assets to ${targetDir}`);
