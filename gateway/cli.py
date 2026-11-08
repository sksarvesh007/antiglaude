"""CLI management interface for Antiglaude Bridge."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    from importlib.metadata import version as _pkg_version

    _VERSION = _pkg_version("antiglaude")
except Exception:
    _VERSION = "1.0.1"

from gateway.auth.project_resolver import project_resolver
from gateway.auth.token_manager import token_manager
from gateway.config import DEFAULT_CONFIG


def cmd_status(args):
    """Check bridge health, OAuth token, and upstream model tier."""
    print("Checking Antiglaude Bridge status...")
    db_paths = token_manager.get_candidate_db_paths()
    print(f"Found {len(db_paths)} local Antigravity state database(s):")
    for p in db_paths:
        print(f"  - {p}")

    # Windows Credential Manager (agy / Antigravity IDE) fallback
    try:
        r_token, email, access_token, expiry = (
            token_manager.extract_refresh_token_from_windows_credential_manager()
        )
        if r_token or access_token:
            print("Found Google OAuth credentials in Windows Credential Manager (gemini:antigravity).")
            if email:
                print(f"  - Account: {email}")
    except Exception:
        pass

    async def _check():
        try:
            token = await token_manager.get_access_token()
            print(f"OAuth Token: Active (len={len(token)})")
            proj, tier = await project_resolver.resolve(token)
            print(f"Project ID: {proj}")
            print(f"Account Tier: {tier}")
        except Exception as e:
            print(f"Auth / Resolver error: {e}")

    asyncio.run(_check())

    # Check if local gateway is running
    url = f"http://{DEFAULT_CONFIG.host}:{DEFAULT_CONFIG.port}/health"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            data = json.loads(resp.read().decode())
            print(f"\nGateway Server: RUNNING on http://{DEFAULT_CONFIG.host}:{DEFAULT_CONFIG.port}")
            print(f"Health response: {data}")
    except Exception:
        print(f"\nGateway Server: NOT running on port {DEFAULT_CONFIG.port} (run `start` to launch)")


def cmd_start(args):
    """Start the gateway server."""
    import uvicorn
    from gateway.server import app

    host = args.host or DEFAULT_CONFIG.host
    port = args.port or DEFAULT_CONFIG.port
    print(f"Starting Antiglaude Bridge Gateway on http://{host}:{port}...")
    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_claude(args):
    """Run Claude Code CLI pointing directly to the bridge."""
    prompt = " ".join(args.prompt) if args.prompt else None
    gateway_url = f"http://{args.host}:{args.port}"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url
    env["ANTHROPIC_API_KEY"] = "bridge-token"
    env["ANTHROPIC_MODEL"] = args.model

    cmd = ["claude"]
    if args.permission_mode:
        cmd.extend(["--permission-mode", args.permission_mode])
    if prompt:
        cmd.extend(["-p", prompt])

    print(f"Running Claude Code via {gateway_url} (model: {args.model})...")
    subprocess.run(cmd, env=env)


def cmd_setup_claude(args):
    """Configure ~/.claude/settings.json with Antiglaude defaults and modelPicker."""
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    data.setdefault("env", {})
    data["env"]["ANTHROPIC_BASE_URL"] = f"http://{args.host}:{args.port}"
    data["env"]["ANTHROPIC_API_KEY"] = "test"
    data["env"]["CLAUDE_CODE_AUTO_MODE_SERVER"] = "0"
    data["env"]["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    data["model"] = args.model

    data["modelPicker"] = {
        "replaceBuiltInOptions": False,
        "options": [
            {
                "model": "claude-sonnet-4-6",
                "label": "Claude Sonnet 4.6 (Thinking)",
                "description": "Fast agentic reasoning via Antiglaude Bridge",
                "behavesAs": "claude-3-7-sonnet",
            },
            {
                "model": "claude-opus-4-6-thinking",
                "label": "Claude Opus 4.6 (Thinking)",
                "description": "Deep reasoning with extended thinking via Antiglaude Bridge",
                "behavesAs": "claude-3-opus",
            },
            {
                "model": "gemini-3-flash",
                "label": "Gemini 3 Flash",
                "description": "High-speed completions via Antiglaude Bridge",
                "behavesAs": "claude-3-5-haiku",
            },
            {
                "model": "gemini-2.5-pro",
                "label": "Gemini 2.5 Pro",
                "description": "Analytical large-context model via Antiglaude Bridge",
                "behavesAs": "claude-3-7-sonnet",
            },
        ],
    }

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Configured Claude Code settings at {settings_path}")
    print(f"  - Gateway URL: http://{args.host}:{args.port}")
    print(f"  - Active Model: {args.model}")
    print("  - /model Selector: Sonnet 4.6, Opus 4.6, Gemini 3 Flash, Gemini 2.5 Pro")


def cmd_tui(args):
    """Launch the interactive terminal Control Panel."""
    from gateway.tui.control_panel import run_control_panel

    run_control_panel()


def cmd_daemon(args):
    """Manage background macOS LaunchAgent daemon."""
    from gateway.daemon.macos_daemon import (
        install_launchagent,
        status_launchagent,
        uninstall_launchagent,
    )

    action = args.action
    if action == "install":
        install_launchagent(port=args.port)
    elif action == "uninstall":
        uninstall_launchagent()
    elif action == "status":
        status_launchagent()
    else:
        print("Unknown daemon action. Choose: install, uninstall, status")


def main():
    parser = argparse.ArgumentParser(description="Antiglaude Bridge CLI")
    parser.add_argument("--version", action="version", version=f"antiglaude {_VERSION}")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # status
    subparsers.add_parser("status", help="Check bridge and token health")

    # start / serve
    for name in ("start", "serve"):
        p = subparsers.add_parser(name, help="Start the Anthropic & OpenAI compatible gateway server")
        p.add_argument("--host", default=DEFAULT_CONFIG.host, help="Host to bind")
        p.add_argument("--port", type=int, default=DEFAULT_CONFIG.port, help="Port to bind")

    # claude
    claude_p = subparsers.add_parser("claude", help="Run Claude Code through the bridge")
    claude_p.add_argument("--host", default=DEFAULT_CONFIG.host, help="Gateway host")
    claude_p.add_argument("--port", type=int, default=DEFAULT_CONFIG.port, help="Gateway port")
    claude_p.add_argument("--model", default="claude-sonnet-4-6", help="Model to use")
    claude_p.add_argument("--permission-mode", default="auto", help="Permission mode")
    claude_p.add_argument("prompt", nargs="*", help="Optional prompt to pass to claude -p")

    # setup-claude
    setup_p = subparsers.add_parser("setup-claude", help="Configure ~/.claude/settings.json with bridge defaults and /model selector")
    setup_p.add_argument("--host", default=DEFAULT_CONFIG.host, help="Gateway host")
    setup_p.add_argument("--port", type=int, default=DEFAULT_CONFIG.port, help="Gateway port")
    setup_p.add_argument("--model", default="claude-sonnet-4-6", help="Default model")

    # tui / panel
    for name in ("tui", "panel"):
        subparsers.add_parser(name, help="Launch interactive Terminal Control Panel")

    # daemon
    daemon_p = subparsers.add_parser("daemon", help="Manage background macOS LaunchAgent service")
    daemon_p.add_argument("action", choices=["install", "uninstall", "status"], help="Action to perform")
    daemon_p.add_argument("--port", type=int, default=DEFAULT_CONFIG.port, help="Port to bind")

    args = parser.parse_args()
    if args.command == "status":
        cmd_status(args)
    elif args.command in ("start", "serve"):
        cmd_start(args)
    elif args.command == "claude":
        cmd_claude(args)
    elif args.command == "setup-claude":
        cmd_setup_claude(args)
    elif args.command in ("tui", "panel"):
        cmd_tui(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
