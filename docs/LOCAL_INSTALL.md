# Local installation and first test

unAI's default test path is local-only: a GGUF model runs through `llama.cpp`, the Python Engine talks to that loopback server, and the React UI talks to the Engine. No hosted model endpoint or API key is required.

## Android / Termux (recommended first test)

Keep the repository, Python environment, llama.cpp build and model under Termux's private home directory. Do not move the live runtime into shared storage.

```bash
pkg update -y
pkg install -y git
git clone --branch feat/m4-instance-methods-m5-specialists --single-branch \
  https://github.com/somildholakia/UnAi.git
cd UnAi
bash scripts/termux-install.sh
bash scripts/termux-start.sh
bash scripts/termux-doctor.sh
```

The installer installs Termux packages, compiles the `llama-server` CPU runtime with two build jobs by default, downloads the Qwen2.5-Coder 1.5B Q4_K_M GGUF file (about 1.12 GB), creates the Engine virtual environment and installs the web dependencies. The download resumes if interrupted. Override `UNAI_BUILD_JOBS`, `UNAI_LLAMA_CPP_DIR`, `UNAI_MODEL_DIR` or `UNAI_MODEL_FILE` before running the installer to change the paths/build concurrency.

After startup, open `http://127.0.0.1:5173` in the browser on the same Android device. The tmux session has three windows: `model`, `engine`, and `web`. Detach with Ctrl+B then D; reattach with `bash scripts/termux-start.sh`; stop with `tmux kill-session -t unai`.

The first build can take time and the first model download needs roughly 1.2 GB of free storage. Start with the 2K context and CPU-only settings; increase context or threads only after observing memory and thermal behavior. Android may terminate a process under memory pressure.

Startup waits for all three loopback services (model, Engine and Web UI), for up to 90 seconds each by default. Change this with `UNAI_STARTUP_TIMEOUT_SECONDS`. A failed wait prints recent output from the corresponding tmux window; startup still opens tmux so you can inspect the failing process.

`scripts/termux-doctor.sh` checks the Termux environment, required commands, executable/model files, Engine virtual environment, Web dependencies and all three service health URLs. Run `bash scripts/termux-doctor.sh --smoke-chat` to additionally send a tiny request directly to the local OpenAI-compatible model endpoint and verify that it returned non-empty text. This tests local inference without sending the prompt to a hosted provider. It does not measure speed, memory or thermal performance.

## Desktop Linux / macOS / Windows

Install a local `llama.cpp` build or another OpenAI-compatible local model server. For a Qwen GGUF file, the upstream `llama-server` command is similar to:

```bash
llama-server -m /absolute/path/to/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 --alias unai-local-coder \
  --ctx-size 2048 --threads 4
```

Then install and run the Engine:

```bash
cd apps/engine
python -m venv .venv
# Activate .venv for your shell, then:
pip install -e .
cp .env.example .env
uvicorn unai_engine.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd apps/web
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

The shipped `.env.example` points to `http://127.0.0.1:8080/v1` and model alias `unai-local-coder`. For Ollama, change these to the local Ollama OpenAI-compatible URL and an installed Ollama model identifier. The Engine and model server bind to loopback; do not expose them to the internet without a separate security review.

## What “local and unlimited” means

Prompts and inference stay on the device when the configured model server is local. This removes hosted-model usage quotas and per-token provider billing for local generation. There is no remote fallback in this setup. It is not literally infinite: available RAM, storage, battery, thermal throttling and inference speed limit practical use. The starter model is a compact open-weight coding model used to make the first install testable; it is not an unAI-trained model and does not establish parity with large hosted models.

## First smoke test

1. Run `bash scripts/termux-doctor.sh` and resolve any failed dependency or endpoint checks.
2. Run `bash scripts/termux-doctor.sh --smoke-chat` to verify that the local model can complete a short request.
3. Open the Web UI and send: `Write a Python function that returns the factorial of a non-negative integer, and include edge cases.`
4. Enable airplane mode after setup and send a second prompt. This checks that inference does not depend on internet access after the model is downloaded.

The installer and scripts do not claim physical-device performance acceptance. Record actual RSS/PSS, model load time, time-to-first-token, tokens/second and thermal behavior as described in [MOBILE_PROFILE.md](MOBILE_PROFILE.md).
