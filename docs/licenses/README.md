# Model License Directory & Commercial Attribution Terms

This directory documents the upstream license terms, attribution requirements, and commercial usage permissions for speech recognition models configured in this platform.

---

## 1. Sherpa-ONNX Streaming Zipformer 20M
* **Model ID:** `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`
* **Upstream Authors:** Next-gen Kaldi / k2-fsa team (`csukuangfj`)
* **Repository:** [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
* **Declared License:** **Apache License 2.0** (SPDX: `Apache-2.0`)
* **Commercial Use:** Permitted royalty-free with standard copyright notice and disclaimer retention.
* **Weights Included:**
  * `encoder-epoch-99-avg-1.onnx` (88,804,590 bytes) / `encoder-epoch-99-avg-1.int8.onnx` (42,845,182 bytes)
  * `decoder-epoch-99-avg-1.onnx` (2,092,272 bytes) / `decoder-epoch-99-avg-1.int8.onnx` (539,499 bytes)
  * `joiner-epoch-99-avg-1.onnx` (1,026,462 bytes) / `joiner-epoch-99-avg-1.int8.onnx` (259,572 bytes)
  * `tokens.txt` (5,048 bytes)

---

## 2. NVIDIA Parakeet Realtime EOU 120M Transducer
* **Model ID:** `parakeet-cpp-realtime-scene` (referencing `realtime_eou_120m-v1-f16.gguf`)
* **Upstream Authors:** NVIDIA NeMo ASR Team
* **Official Model Card:** [nvidia/parakeet_realtime_eou_120m-v1](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1)
* **Declared License:** **NVIDIA Open Model License**
* **Commercial Use:** Permitted under the NVIDIA Open Model License terms, allowing commercial deployment and derivative distribution with attribution. *(Note: Earlier documentation incorrectly cited CC-BY-4.0 from `parakeet-tdt-1.1b`; this has been corrected).*
* **Bundled Scene Components:**
  * Acoustic model: `realtime_eou_120m-v1-f16.gguf` (transducer with End-Of-Utterance token)
  * Diarization: `nemotron-3-diarization-q8_0.gguf`
  * Sound event detection: `ced-tiny-q8_0.gguf`

---

## 3. NVIDIA Nemotron 3.5 ASR Streaming 0.6B
* **Model ID:** `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b`
* **Upstream Authors:** NVIDIA NeMo ASR Team
* **Official Model Card:** [nvidia/nemotron-3.5-asr-streaming-0.6b](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
* **Declared License:** **NVIDIA Open Model License Agreement / OpenMDW-1.1**
* **Commercial Use:** Permitted for commercial inference and application integration, subject to standard acceptable use guidelines and attribution.
* **Inference Post-Processing Note:** Appends a trailing locale tag (`<en-US>`) that should be stripped by gateway proxies before feeding LLM conversational context.
