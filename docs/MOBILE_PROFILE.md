# Mobile profile: 6 GB RAM, CPU-first

Status: requirements and runtime research only; no physical-device performance results have yet been recorded.

## Memory budget (initial targets, not measurements)

| Component | Budget |
| --- | ---: |
| Model + inference runtime PSS | <= 1.2 GB |
| Whole unAI app/process peak | <= 1.8 GB |
| OS, foreground apps and headroom | remainder |

This is a stringent target; model weights, KV cache, tokenizer, JNI/native runtime, retrieval index, and temporary buffers all count. A compressed model's file size is not the same as runtime memory.

## Runtime policy

- CPU must be the guaranteed execution path; GPU/NPU is optional.
- Default generation context starts at 2K tokens. Increase only when measured peak memory permits it.
- Use SQLite and a disk-backed repository index. Avoid loading whole repositories or complete transcripts into RAM.
- Load specialist components on demand; release their memory after task completion.
- React to Android memory pressure by shrinking candidate width, context, cache, and retrieval batch size before the process is killed.
- No remote inference fallback without explicit user opt-in.
- Install model assets once, verify hashes, and support offline use afterwards.

## Device benchmark record template

- Device / RAM / chipset:
- Android version / kernel:
- Runtime + commit:
- Model + quantization + file size:
- Context / output tokens:
- Idle PSS / peak PSS:
- Load time / TTFT / decode tokens per second:
- Energy / battery delta:
- Thermal state and sustained rate after 10 minutes:
- Task pass rate and regression rate:
- Airplane mode verified:

Do not call mobile support complete until this record is populated on at least one physical 6 GB device.


## First local runtime preset

The current Android smoke-test preset uses `Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF` at `Q4_K_M`, served by a CPU-only `llama.cpp` process with a 2K context. This is an installability baseline, not a measured fit or performance guarantee for every 6 GB device. The GGUF file is approximately 1.12 GB; runtime memory additionally includes the KV cache, graph buffers, tokenizer and application processes. Start with the supplied thread/context settings and collect the device record above before increasing them.

The Termux scripts keep the model and llama.cpp build in `$HOME`, bind inference/API/UI listeners to `127.0.0.1`, and provide no cloud fallback. See [LOCAL_INSTALL.md](LOCAL_INSTALL.md).
