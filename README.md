# Antiglaude

[![PyPI](https://img.shields.io/pypi/v/antiglaude?label=pypi&color=111111)](https://pypi.org/project/antiglaude/)
[![CI](https://github.com/sksarvesh007/antiglaude/actions/workflows/ci.yml/badge.svg)](https://github.com/sksarvesh007/antiglaude/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/sksarvesh007/antiglaude?color=111111)](https://github.com/sksarvesh007/antiglaude/releases)
[![Python](https://img.shields.io/badge/python-3.10+-111111.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/packaging-uv-111111.svg)](https://github.com/astral-sh/uv)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-111111.svg)](https://fastapi.tiangolo.com)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Compatible-111111.svg)](https://code.claude.com)
[![OpenAI Compatible](https://img.shields.io/badge/OpenAI%20API-Compatible-111111.svg)](https://platform.openai.com)
[![License](https://img.shields.io/badge/license-MIT-111111.svg)](https://opensource.org/licenses/MIT)

A high-performance local protocol gateway and inference server. Antiglaude translates Anthropic Messages and OpenAI Chat Completions requests into Google Cloud Code and Gemini execution formats, enabling Claude Code CLI and standard AI toolchains to operate against upstream models including `claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gemini-3-flash`, and `gemini-2.5-pro`.

> [!WARNING]
> **Use at your own risk**: Google has been actively detecting, suspending, and banning accounts using automated gateways and unofficial protocol proxies. Do not connect your primary personal or workplace Google account. Always use a dedicated burner account. You assume full responsibility for any account actions, quota limits, or service terminations.

---

## Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Terminal Control Panel](#terminal-control-panel)
- [Claude Code Integration](#claude-code-integration)
  - [In-Session Model Picker (/model)](#in-session-model-picker-model)
  - [System-Wide Persistence](#system-wide-persistence)
  - [Auto Mode Classifier Billing Protocol](#auto-mode-classifier-billing-protocol)
- [Background Service (macOS launchd)](#background-service-macos-launchd)
- [OpenAI Compatible API](#openai-compatible-api)
  - [Python SDK](#python-sdk)
  - [Streaming](#streaming)
  - [HTTP Request](#http-request)
  - [Model Directory](#model-directory)
  - [Third-Party Integrations](#third-party-integrations)
- [Architecture](#architecture)
- [Makefile Reference](#makefile-reference)
- [Testing](#testing)
- [Security and Privacy](#security-and-privacy)
- [License](#license)

---

## Overview

Interfacing external developer tooling with enterprise cloud backends introduces protocol impedance mismatches:

- **Token Expiration**: Upstream access tokens expire after 60 minutes. Antiglaude Bridge extracts permanent refresh credentials from local application state and transparently renews access tokens in the background.
- **Protobuf Schema Incompatibilities**: Advanced JSON Schema keywords emitted by agentic runtimes (`propertyNames`, `anyOf`, `const`) cause HTTP 400 rejection in Google protobuf serializers. The bridge applies bidirectional AST normalizations to guarantee full tool calling compatibility.
- **Header & Identity Filtering**: Third-party billing headers and self-identification banners trip strict upstream quota filters. The gateway neutralizes identity markers and scrubs unsupported metadata.
- **Keep-Alive Resilience**: Long reasoning turns can trigger client connection drops. The gateway emits periodic SSE keep-alive heartbeats and handles TCP resets with pooled connection limits and instant retry failover.

---

## Quick Start

### Option A: Install from PyPI

Install the CLI toolchain via `pip` or Astral's `uv`:

```bash
# Using pip
pip install antiglaude

# Or using uv tool
uv tool install antiglaude

# Or run instantly without installing
uvx antiglaude start
```

Once installed, use the `antiglaude` CLI directly:

```bash
# Start the gateway server
antiglaude start

# Launch the live Terminal Control Panel
antiglaude tui

# Configure Claude Code system-wide (/model picker + default bridge)
antiglaude setup-claude

# Install macOS LaunchAgent daemon (auto-starts on boot)
antiglaude daemon install
```

### TUI 
<img width="1280" height="802" alt="image" src="https://github.com/user-attachments/assets/5ca37c25-7bf8-43fc-a2ce-d2e779bc7801" />

### Option B: Clone from Source

For development or direct Makefile execution via Astral's `uv`:

```bash
git clone https://github.com/sksarvesh007/antiglaude.git
cd antiglaude

# Bootstrap virtual environment and start gateway on port 8080
make start

# Configure Claude Code system-wide and install macOS background daemon
make setup-claude
make daemon-install
```

---

## Terminal Control Panel

Antiglaude Bridge includes a terminal-based dashboard for real-time monitoring of daily request quotas, account identity, upstream latency, and inference logs.

```bash
make tui
```

```
+--------------------------------------------------------------------------------------------------------+
| ANTIGLAUDE BRIDGE  |  ONLINE (http://127.0.0.1:8080)  |  Default: claude-sonnet-4-6  |  2026-11-04   |
+--------------------------------------------------------------------------------------------------------+
| Upstream Authentication                    | Rate Limits & Quota Meter                                 |
| Account:        user@antigravity           | Daily Limit:        1,500 reqs/day                        |
| Project ID:     aicode-consumers           | Requests Today:     14 [==......................] 0.9%    |
| Tier:           Google AI Pro              | Requests Remaining: 1,486                                 |
| Service:        Active (launchd)           | Lifetime Requests:  248                                   |
| Uptime:         1,840 seconds              | Tokens (In / Out):  104,200 / 14,800                      |
+--------------------------------------------+-----------------------------------------------------------+
| Routing Matrix & Supported Models                                                                      |
| Model Identifier           Target Backend                 Status      Requests  Description            |
| > claude-sonnet-4-6        Claude 3.7 Sonnet (Thinking)   Active            12  Primary agentic engine |
|   claude-opus-4-6-thinking Claude 3 Opus (Thinking)       Active             2  Deep reasoning & arch  |
|   gemini-3-flash           Gemini 3 Flash                 Active             0  Low-latency throughput |
|   gemini-2.5-pro           Gemini 2.5 Pro                 Active             0  Large context analysis |
+--------------------------------------------------------------------------------------------------------+
| Request Journal & Latency Log                                                                          |
| Time      Client       Endpoint             Model              Tokens    Latency  Status               |
| 02:49:09  claude-code  /v1/messages         claude-sonnet-4-6  23,609    1820 ms  200                  |
+--------------------------------------------------------------------------------------------------------+
| [r] Refresh   [m] Switch Model   [c] Run Claude   [s] Restart Server   [d] Daemon Manager   [q] Quit   |
+--------------------------------------------------------------------------------------------------------+
```

### Keybindings

| Key | Action |
| :--- | :--- |
| `r` | Force refresh account tier, token status, and quota metrics |
| `m` | Switch active default model in `~/.claude/settings.json` |
| `c` | Launch interactive Claude Code session within the terminal |
| `s` | Restart local gateway process and clear stale network sockets |
| `d` | Open macOS LaunchAgent daemon management dialog |
| `q` | Exit the dashboard |

---

## Claude Code Integration

### In-Session Model Picker (/model)

Claude Code supports native model switching via the `/model` command when configured with a valid `modelPicker` manifest. Run:

```bash
make setup-claude
```

This writes the required schema to `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8080",
    "ANTHROPIC_API_KEY": "test",
    "CLAUDE_CODE_AUTO_MODE_SERVER": "0",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"
  },
  "model": "claude-sonnet-4-6",
  "modelPicker": {
    "replaceBuiltInOptions": false,
    "options": [
      {
        "model": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6 (Thinking)",
        "description": "Fast agentic reasoning via Antiglaude Bridge",
        "behavesAs": "claude-3-7-sonnet"
      },
      {
        "model": "claude-opus-4-6-thinking",
        "label": "Claude Opus 4.6 (Thinking)",
        "description": "Deep reasoning with extended thinking via Antiglaude Bridge",
        "behavesAs": "claude-3-opus"
      },
      {
        "model": "gemini-3-flash",
        "label": "Gemini 3 Flash",
        "description": "High-speed completions via Antiglaude Bridge",
        "behavesAs": "claude-3-5-haiku"
      },
      {
        "model": "gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "description": "Analytical large-context model via Antiglaude Bridge",
        "behavesAs": "claude-3-7-sonnet"
      }
    ]
  }
}
```

Typing `/model` inside any Claude Code session displays the interactive selection menu with full keyboard navigation.

### System-Wide Persistence

The `make setup-claude` command sets configurations in `~/.claude/settings.json` and exports environment variables to `~/.zshrc`. After setup, you can launch Claude Code from any terminal or process without passing flags:

```bash
claude
```

### Auto Mode Classifier Billing Protocol

Claude Code versions 2.1+ include an auto-mode classifier protocol. Local gateways that do not return structured safeguard evaluations trigger billing fallback warnings.

Antiglaude Bridge resolves this through two complementary mechanisms:
1. **Safeguard Protocol Handling**: The bridge parses the `safeguards` request parameter and returns structured `safeguard_results` matching Claude Code's internal schema (`dangerous_tool_use`, status `available`, outcome `not_flagged`).
2. **Local Classifier Delegation**: `CLAUDE_CODE_AUTO_MODE_SERVER="0"` instructs the CLI harness to perform classifications locally without external billing queries.

---

## Background Service (macOS launchd)

To run Antiglaude Bridge permanently in the background:

```bash
# Register and start launchd daemon
make daemon-install
```

### Operational Characteristics
- Installed to `~/Library/LaunchAgents/com.antiglaude.bridge.plist`.
- Automatically loads on macOS login and runs continuously with `KeepAlive: true`.
- Starts before Antigravity IDE or Claude Code CLI sessions open; port 8080 is always available.
- Logs are written to `~/.antiglaude/bridge.stdout.log` and `~/.antiglaude/bridge.stderr.log`.

```bash
# Check status or inspect logs
make daemon-status

# Stop and unregister daemon
make daemon-uninstall
```

---

## OpenAI Compatible API

Antiglaude Bridge provides a drop-in `/v1/chat/completions` endpoint for standard OpenAI SDKs and third-party AI frameworks.

### Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="sk-antiglaude",
)

response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[
        {"role": "system", "content": "You are a systems engineer."},
        {"role": "user", "content": "Describe memory paging in one sentence."},
    ],
)

print(response.choices[0].message.content)
```

### Streaming

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="sk-antiglaude",
)

stream = client.chat.completions.create(
    model="claude-opus-4-6-thinking",
    messages=[{"role": "user", "content": "Write a quicksort implementation in C."}],
    stream=True,
)

for chunk in stream:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

### HTTP Request

```bash
# Non-streaming
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Ping"}]
  }'

# Streaming
curl -N -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{
    "model": "gemini-3-flash",
    "messages": [{"role": "user", "content": "Count from 1 to 5"}],
    "stream": true
  }'
```

### Model Directory

| Model Identifier | Target Backend | Optimized Workload |
| :--- | :--- | :--- |
| `claude-sonnet-4-6` *(default)* | Claude 3.7 Sonnet | Agentic workflows, coding, tool calling |
| `claude-opus-4-6-thinking` | Claude 3 Opus (Thinking) | Complex reasoning, mathematical analysis, architecture |
| `gemini-3-flash` | Gemini 3 Flash | Low-latency completions, summaries, high concurrency |
| `gemini-2.5-pro` | Gemini 2.5 Pro | Deep analytical processing, large-context windows |
| `claude-3-7-sonnet-latest` | `claude-sonnet-4-6` | Anthropic alias fallback |
| `claude-3-5-haiku-latest` | `gemini-3-flash` | Lightweight Anthropic alias fallback |

*Heuristic Fallback: Any arbitrary model identifier requested by a client (such as `gpt-4o` or legacy strings) is mapped to the most capable active upstream model while returning the requested ID in the completion response.*

### Third-Party Integrations

Configure your environment or client configuration:

- **LangChain / LlamaIndex**: Set `base_url="http://127.0.0.1:8080/v1"`, `api_key="test"`.
- **Cursor / Continue.dev**: Add custom OpenAI provider pointing to `http://127.0.0.1:8080/v1`.
- **AutoGen / CrewAI**: Set `base_url="http://127.0.0.1:8080/v1"`.

---

## Architecture

```
+----------------------------------+     +----------------------------------+
|         Claude Code CLI          |     |      OpenAI Compatible SDKs      |
|    Anthropic Messages Protocol   |     |    LangChain, Cursor, Continue   |
+-----------------+----------------+     +-----------------+----------------+
                  |                                        |
                  | POST /v1/messages                      | POST /v1/chat/completions
                  +-------------------+--------------------+
                                      |
                                      v
+---------------------------------------------------------------------------+
|                         Antiglaude Bridge Gateway                         |
|                                                                           |
|   [Anthropic SSE Parser]                 [OpenAI Request/Response Bridge] |
|   - Stream chunk normalizer              - SSE event-to-chunk mapper      |
|   - Safeguard result emitter             - Token usage synthesizer        |
|                                                                           |
|   [Protocol & Schema Transformation]                                      |
|   - OpenAPI-to-Protobuf schema sanitizer (strips propertyNames, anyOf)    |
|   - System turn extraction & identity scrubber                            |
|                                                                           |
|   [Authentication & Reliability]                                          |
|   - Zero-touch OAuth token auto-refresher (permanent refresh credentials) |
|   - Rate-limit backoff engine & TCP keep-alive heartbeat watchdog         |
|   - Live telemetry journal & 1,500 daily quota tracking meter             |
+-------------------------------------+-------------------------------------+
                                      |
                                      | Cloud Code REST / Protobuf
                                      v
+---------------------------------------------------------------------------+
|                  Google Cloud Code / Gemini Inference API                 |
|   Claude Sonnet 4.6 (Thinking)         Gemini 3 Flash                     |
|   Claude Opus 4.6 (Thinking)           Gemini 2.5 Pro                     |
+---------------------------------------------------------------------------+
```

---

## Makefile Reference

| Target | Description | Example |
| :--- | :--- | :--- |
| `make start` | Installs dependencies with `uv` and starts the gateway | `make start` |
| `make serve` | Alias for `make start` | `make serve PORT=8080` |
| `make tui` | Opens the terminal control panel | `make tui` |
| `make setup-claude` | Configures `~/.claude/settings.json` and `/model` menu | `make setup-claude` |
| `make daemon-install` | Installs and loads macOS LaunchAgent service | `make daemon-install` |
| `make daemon-status` | Displays LaunchAgent process status and log output | `make daemon-status` |
| `make daemon-uninstall` | Stops and unregisters the LaunchAgent service | `make daemon-uninstall` |
| `make claude` | Starts Claude Code CLI routed through the local bridge | `make claude` |
| `make status` | Verifies token validity and upstream account resolution | `make status` |
| `make test` | Executes the test harness | `make test` |
| `make stop` | Terminates processes listening on the configured port | `make stop PORT=8080` |
| `make clean` | Removes virtual environment, build artifacts, and cache | `make clean` |

---

## Testing

The test harness validates protocol conversions, schema sanitization, streaming event framing, and endpoint compatibility:

```bash
make test
```

All 18 unit tests execute in isolated test environments without consuming upstream inference quota.

---

## Security and Privacy

- **Local Execution**: The gateway binds exclusively to loopback (`127.0.0.1`).
- **No Third-Party Telemetry**: Traffic flows directly between local clients and the configured Google infrastructure.
- **Credential Hygiene**: Local state databases, OAuth tokens, and log directories are excluded from version control via `.gitignore`.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
