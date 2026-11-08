"""Terminal User Interface (TUI) Control Panel for Antiglaude Bridge.

Designed for complete visual stability:
- Single-instance cbreak terminal lifecycle (no per-tick tcsetattr screen churn)
- In-place modal sub-views (zero screen buffer flips or terminal resets)
- Fully dynamic height scaling that guarantees layouts never overflow screen bounds
- Single-threaded synchronous rendering eliminating frame tearing and flicker
"""

import asyncio
import json
import os
import select
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

from rich.align import Align
from rich.box import ROUNDED, SIMPLE
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gateway.auth.project_resolver import project_resolver
from gateway.auth.token_manager import token_manager
from gateway.config import DEFAULT_CONFIG
from gateway.telemetry.stats_tracker import stats_tracker

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

# View Identifiers
VIEW_DASHBOARD = "dashboard"
VIEW_MODELS = "models"
VIEW_DAEMON = "daemon"
VIEW_HELP = "help"

MODELS_CATALOG: List[Tuple[str, str, str, str]] = [
    ("1", "claude-sonnet-4-6", "Claude 3.7 Sonnet (Thinking)", "Primary agentic reasoning engine for Claude Code CLI"),
    ("2", "claude-opus-4-6-thinking", "Claude 3 Opus (Extended Thinking)", "Deep analytical & complex architectural problem solving"),
    ("3", "gemini-3-flash", "Gemini 3 Flash", "Ultra-fast completions, syntax checks, and lightweight calls"),
    ("4", "gemini-2.5-pro", "Gemini 2.5 Pro", "Massive context analysis and full codebase ingestion"),
]


class RawTerminal:
    """Manages raw/cbreak terminal mode lifecycle without repeatedly resetting settings."""

    def __init__(self):
        self.fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        self.old_settings = None

    def __enter__(self):
        if self.fd is not None and termios is not None and sys.platform != "win32":
            try:
                self.old_settings = termios.tcgetattr(self.fd)
                tty.setcbreak(self.fd)
            except Exception:
                pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd is not None and termios is not None and sys.platform != "win32":
            self.restore()

    def restore(self):
        if self.fd is not None and self.old_settings is not None and termios is not None and sys.platform != "win32":
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass
            self.old_settings = None

    def read_key(self, timeout: float = 0.4) -> Optional[str]:
        """Read a single keypress or escape sequence non-blockingly."""
        if self.fd is None or msvcrt is None:
            time.sleep(timeout)
            return None

        # Windows console input via msvcrt
        if sys.platform == "win32":
            start = time.monotonic()
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        # Function/arrows prefix; consume the second byte and poll again
                        _ = msvcrt.getwch()
                        continue
                    return ch
                if time.monotonic() - start >= timeout:
                    return None
            return None

        if self.fd is None:
            time.sleep(timeout)
            return None
        try:
            rlist, _, _ = select.select([self.fd], [], [], timeout)
            if rlist:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # Escape or sequence
                    rlist2, _, _ = select.select([self.fd], [], [], 0.05)
                    if rlist2:
                        seq = sys.stdin.read(2)
                        return f"\x1b{seq}"
                    return "ESC"
                return ch
        except Exception:
            return None
        return None


def get_live_server_data() -> Optional[Dict[str, Any]]:
    """Query the local gateway /v1/gateway/stats endpoint with tight timeout."""
    url = f"http://{DEFAULT_CONFIG.host}:{DEFAULT_CONFIG.port}/v1/gateway/stats"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Antiglaude-TUI"})
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        pass
    return None


def get_current_claude_model() -> str:
    """Read current default model configured in ~/.claude/settings.json."""
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("model", "claude-sonnet-4-6")
    except Exception:
        pass
    return "claude-sonnet-4-6"


def set_current_claude_model(new_model: str) -> bool:
    """Update default model in ~/.claude/settings.json."""
    try:
        data = {}
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["model"] = new_model
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def is_daemon_running() -> bool:
    """Check if macOS LaunchAgent or server daemon is active."""
    try:
        res = subprocess.run(
            ["launchctl", "list", "com.antiglaude.bridge"],
            capture_output=True,
            text=True,
        )
        return res.returncode == 0
    except Exception:
        return False


def restart_gateway_server() -> bool:
    """Restart local gateway server process."""
    try:
        subprocess.run(
            f"lsof -ti :{DEFAULT_CONFIG.port} | xargs kill -9 2>/dev/null || true",
            shell=True,
            capture_output=True,
        )
        cmd = [sys.executable, "-m", "gateway.cli", "start", "--port", str(DEFAULT_CONFIG.port)]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def build_tui_layout(
    console: Console,
    stats: Dict[str, Any],
    is_server_online: bool,
    active_claude_model: str,
    current_view: str,
    banner_message: Optional[str] = None,
) -> Layout:
    """Build a mathematically bounded, non-overflowing Rich Layout."""
    h = max(console.height, 20)
    w = max(console.width, 60)

    header_size = 3
    footer_size = 3
    remaining = h - header_size - footer_size

    layout = Layout()

    # Determine vertical partition to fit terminal lines exactly
    if remaining >= 20:
        upper_size = 7
        center_size = 7
        journal_size = remaining - upper_size - center_size
        layout.split_column(
            Layout(name="header", size=header_size),
            Layout(name="upper", size=upper_size),
            Layout(name="center", size=center_size),
            Layout(name="journal", size=journal_size),
            Layout(name="footer", size=footer_size),
        )
    else:
        upper_size = min(7, remaining // 2)
        center_size = remaining - upper_size
        journal_size = 0
        layout.split_column(
            Layout(name="header", size=header_size),
            Layout(name="upper", size=upper_size),
            Layout(name="center", size=center_size),
            Layout(name="footer", size=footer_size),
        )

    layout["upper"].split_row(
        Layout(name="account_card", ratio=1),
        Layout(name="quota_card", ratio=1),
    )

    # 1. Header
    header_text = Text()
    header_text.append(" ANTIGLAUDE BRIDGE ", style="bold white on #262626")
    header_text.append(" │ ", style="dim")
    if is_server_online:
        header_text.append("● ONLINE", style="bold green")
        header_text.append(f" (http://{DEFAULT_CONFIG.host}:{DEFAULT_CONFIG.port})", style="dim green")
    else:
        header_text.append("○ OFFLINE", style="bold red")
        header_text.append(" (Press [s] to Start)", style="dim yellow")

    header_text.append(" │ ", style="dim")
    header_text.append("Default: ", style="dim")
    header_text.append(f"{active_claude_model}", style="bold cyan")
    header_text.append(" │ ", style="dim")
    header_text.append(f"{datetime.now().strftime('%H:%M:%S')}", style="dim")

    layout["header"].update(Panel(Align.center(header_text), box=ROUNDED, style="dim white"))

    # 2. Account Card
    acc = stats.get("account", {})
    acc_table = Table(box=None, show_header=False, pad_edge=False, expand=True)
    acc_table.add_column("Key", style="dim white", width=14)
    acc_table.add_column("Val", style="bold cyan")

    acc_table.add_row("Account:", f"[white]{acc.get('email', 'default@antigravity')}[/]")
    acc_table.add_row("Project ID:", f"[dim cyan]{acc.get('project_id', 'aicode-consumers')}[/]")
    acc_table.add_row("Tier:", f"[green]{acc.get('tier_name', 'Google AI Pro')}[/]")
    acc_table.add_row("Daemon:", "[green]Active (launchd)[/]" if is_daemon_running() else "[dim]Inactive[/]")
    acc_table.add_row("Uptime:", f"{stats.get('uptime_seconds', 0):,}s")

    layout["upper"]["account_card"].update(
        Panel(acc_table, title="[bold]Upstream Authentication[/]", box=ROUNDED, style="dim white")
    )

    # 3. Quota Card
    daily_limit = stats.get("daily_limit", 1500)
    requests_today = stats.get("requests_today", 0)
    requests_remaining = stats.get("requests_remaining", daily_limit)
    pct_used = stats.get("quota_percent_used", 0.0)

    bar_width = 16 if w < 100 else 24
    filled = int((pct_used / 100.0) * bar_width)
    bar_color = "green" if pct_used < 60 else "yellow" if pct_used < 85 else "red"
    bar_str = f"[{bar_color}]{'█' * filled}[/][dim]{'░' * (bar_width - filled)}[/] {pct_used}%"

    quota_table = Table(box=None, show_header=False, pad_edge=False, expand=True)
    quota_table.add_column("Key", style="dim white", width=14)
    quota_table.add_column("Val", style="bold")

    quota_table.add_row("Daily Limit:", f"[white]{daily_limit:,} reqs/day[/]")
    quota_table.add_row("Requests Today:", f"[yellow]{requests_today:,}[/] ({bar_str})")
    quota_table.add_row("Remaining:", f"[bold green]{requests_remaining:,}[/]")
    quota_table.add_row("Lifetime Reqs:", f"[white]{stats.get('total_requests', 0):,}[/]")
    quota_table.add_row("Tokens (In/Out):", f"[dim]{stats.get('total_input_tokens', 0):,} / {stats.get('total_output_tokens', 0):,}[/]")

    layout["upper"]["quota_card"].update(
        Panel(quota_table, title="[bold]Daily Quota & Telemetry[/]", box=ROUNDED, style="dim white")
    )

    # 4. Center Section: In-Place Modal Switching (No buffer flashing)
    if current_view == VIEW_MODELS:
        models_switch_table = Table(box=None, show_header=False, expand=True, pad_edge=False)
        models_switch_table.add_column("Key", width=5, style="bold yellow")
        models_switch_table.add_column("Identifier", width=26, style="bold white")
        models_switch_table.add_column("Target Backend", width=30, style="cyan")
        models_switch_table.add_column("Status", width=16)

        for key, m_id, m_target, _ in MODELS_CATALOG:
            is_cur = (m_id == active_claude_model)
            status_tag = "[bold green]► Active Default[/]" if is_cur else "[dim]Available[/]"
            row_style = "bold white" if is_cur else "dim white"
            models_switch_table.add_row(f"[{key}]", m_id, m_target, status_tag, style=row_style)

        layout["center"].update(
            Panel(
                models_switch_table,
                title="[bold cyan]Select Default Model [Press 1-4 to select, Esc to return][/]",
                box=ROUNDED,
                style="cyan",
            )
        )

    elif current_view == VIEW_DAEMON:
        daemon_table = Table(box=None, show_header=False, expand=True, pad_edge=False)
        daemon_table.add_column("Key", width=5, style="bold yellow")
        daemon_table.add_column("Action", style="white")

        is_running = is_daemon_running()
        status_text = "[bold green]Installed and Active (launchd)[/]" if is_running else "[dim yellow]Not Running[/]"

        daemon_table.add_row("", f"Current Status: {status_text}")
        daemon_table.add_row("[1]", "Install & Start macOS LaunchAgent (Auto-starts on login)")
        daemon_table.add_row("[2]", "Stop & Uninstall LaunchAgent")
        daemon_table.add_row("[3]", "Restart LaunchAgent Service")
        daemon_table.add_row("[0]", "Return to Main Dashboard")

        layout["center"].update(
            Panel(
                daemon_table,
                title="[bold cyan]macOS LaunchAgent Manager [Press 1-3, Esc to return][/]",
                box=ROUNDED,
                style="cyan",
            )
        )

    elif current_view == VIEW_HELP:
        help_table = Table(box=None, show_header=False, expand=True, pad_edge=False)
        help_table.add_column("Key", width=6, style="bold yellow")
        help_table.add_column("Description", style="white")

        help_table.add_row("[r]", "Refresh live telemetry and account quotas from gateway")
        help_table.add_row("[m]", "Open in-place model selector to update ~/.claude/settings.json")
        help_table.add_row("[s]", "Restart/start local bridge server process on port 8080")
        help_table.add_row("[d]", "Manage macOS LaunchAgent background service")
        help_table.add_row("[q]", "Exit Control Panel cleanly")

        layout["center"].update(
            Panel(
                help_table,
                title="[bold cyan]Navigation & Shortcuts [Press Esc or any key to return][/]",
                box=ROUNDED,
                style="cyan",
            )
        )

    else:
        # Default: Models Routing Matrix
        models_table = Table(box=None, show_header=True, expand=True, pad_edge=False)
        models_table.add_column("Model Identifier", style="bold cyan", width=28)
        models_table.add_column("Target Backend", style="white", width=28)
        models_table.add_column("Status", style="green", width=12)
        models_table.add_column("Requests", justify="right", style="yellow", width=10)
        models_table.add_column("Description", style="dim", ratio=1)

        usage = stats.get("models_usage", {})
        for _, m_name, m_alias, m_desc in MODELS_CATALOG:
            is_cur = (m_name == active_claude_model)
            m_disp = f"► {m_name}" if is_cur else f"  {m_name}"
            m_cnt = usage.get(m_name, 0)
            style = "bold white" if is_cur else None
            models_table.add_row(m_disp, m_alias, "● Active", f"{m_cnt:,}", m_desc, style=style)

        layout["center"].update(
            Panel(models_table, title="[bold]Routing Matrix & Supported Models[/]", box=ROUNDED, style="dim white")
        )

    # 5. Journal Section (Only displayed if terminal has sufficient vertical space)
    if journal_size > 0:
        journal_table = Table(box=SIMPLE, expand=True, pad_edge=False)
        journal_table.add_column("Time", style="dim", width=10)
        journal_table.add_column("Client", style="dim cyan", width=14)
        journal_table.add_column("Endpoint", style="white", width=20)
        journal_table.add_column("Model", style="yellow", width=24)
        journal_table.add_column("Tokens", justify="right", style="green", width=10)
        journal_table.add_column("Latency", justify="right", style="dim white", width=10)
        journal_table.add_column("Status", justify="center", width=8)

        recent = stats.get("recent_requests", [])
        max_rows = max(1, journal_size - 3)
        if not recent:
            journal_table.add_row("-", "-", "No recent requests logged", "-", "-", "-", "-")
        else:
            for r in list(reversed(recent))[:max_rows]:
                status_code = r.get("status_code", 200)
                status_style = "green" if status_code < 400 else "red"
                journal_table.add_row(
                    r.get("time_str", "-"),
                    r.get("client", "claude-code"),
                    r.get("endpoint", "/v1/messages"),
                    r.get("model", "-"),
                    f"{r.get('total_tokens', 0):,}",
                    f"{r.get('latency_ms', 0):.0f}ms",
                    Text(str(status_code), style=status_style),
                )

        layout["journal"].update(
            Panel(journal_table, title="[bold]Request Journal & Latency Log[/]", box=ROUNDED, style="dim white")
        )

    # 6. Footer Controls / Banner
    footer_text = Text()
    if banner_message:
        footer_text.append(f" {banner_message} ", style="bold black on green")
        footer_text.append("  ", style="dim")

    if current_view == VIEW_MODELS:
        footer_text.append(" [1-4] ", style="bold black on white")
        footer_text.append(" Select Model   ", style="dim white")
        footer_text.append(" [Esc/0] ", style="bold black on white")
        footer_text.append(" Cancel & Return", style="dim white")
    elif current_view == VIEW_DAEMON:
        footer_text.append(" [1] ", style="bold black on white")
        footer_text.append(" Install   ", style="dim white")
        footer_text.append(" [2] ", style="bold black on white")
        footer_text.append(" Uninstall   ", style="dim white")
        footer_text.append(" [3] ", style="bold black on white")
        footer_text.append(" Restart   ", style="dim white")
        footer_text.append(" [Esc/0] ", style="bold black on white")
        footer_text.append(" Return", style="dim white")
    elif current_view == VIEW_HELP:
        footer_text.append(" [Esc] ", style="bold black on white")
        footer_text.append(" Return to Dashboard", style="dim white")
    else:
        footer_text.append(" [r] ", style="bold black on white")
        footer_text.append(" Refresh   ", style="dim white")
        footer_text.append(" [m] ", style="bold black on white")
        footer_text.append(" Switch Model   ", style="dim white")
        footer_text.append(" [s] ", style="bold black on white")
        footer_text.append(" Restart Server   ", style="dim white")
        footer_text.append(" [d] ", style="bold black on white")
        footer_text.append(" Daemon Manager   ", style="dim white")
        footer_text.append(" [?] ", style="bold black on white")
        footer_text.append(" Help   ", style="dim white")
        footer_text.append(" [q] ", style="bold white on red")
        footer_text.append(" Quit", style="dim white")

    layout["footer"].update(Panel(Align.center(footer_text), box=ROUNDED, style="dim white"))

    return layout


def run_control_panel():
    """Main execution loop for TUI."""
    console = Console()

    # Pre-warm project & tier info in background
    async def _init_auth():
        try:
            token = await token_manager.get_access_token()
            await project_resolver.resolve(token)
        except Exception:
            pass

    try:
        asyncio.run(_init_auth())
    except Exception:
        pass

    current_view = VIEW_DASHBOARD
    banner_message: Optional[str] = None
    banner_expires: float = 0.0

    last_poll_time = 0.0
    cached_live_data: Optional[Dict[str, Any]] = None

    # Single-instance raw terminal context manager (avoids tcsetattr churn)
    with RawTerminal() as terminal:
        # Controlled single-threaded live rendering with alternate screen
        with Live(console=console, screen=True, auto_refresh=False) as live:
            try:
                while True:
                    now = time.time()

                    # Check banner expiration
                    if banner_message and now > banner_expires:
                        banner_message = None

                    # Poll server stats every 1.5 seconds or on initial tick
                    if now - last_poll_time > 1.5 or cached_live_data is None:
                        cached_live_data = get_live_server_data()
                        last_poll_time = now

                    is_online = cached_live_data is not None
                    stats = cached_live_data or stats_tracker.get_snapshot()
                    active_model = get_current_claude_model()

                    # Render frame
                    layout = build_tui_layout(
                        console=console,
                        stats=stats,
                        is_server_online=is_online,
                        active_claude_model=active_model,
                        current_view=current_view,
                        banner_message=banner_message,
                    )
                    live.update(layout, refresh=True)

                    # Non-blocking keyread
                    key = terminal.read_key(timeout=0.35)
                    if not key:
                        continue

                    # Key navigation handler
                    if current_view == VIEW_DASHBOARD:
                        if key in ("q", "Q"):
                            break
                        elif key in ("r", "R"):
                            cached_live_data = get_live_server_data()
                            last_poll_time = time.time()
                            banner_message = "Telemetry Refreshed"
                            banner_expires = time.time() + 2.0
                        elif key in ("m", "M"):
                            current_view = VIEW_MODELS
                        elif key in ("d", "D"):
                            current_view = VIEW_DAEMON
                        elif key == "?":
                            current_view = VIEW_HELP
                        elif key in ("s", "S"):
                            restart_gateway_server()
                            cached_live_data = None
                            banner_message = "Gateway Server Restarted (Port 8080)"
                            banner_expires = time.time() + 3.0

                    elif current_view == VIEW_MODELS:
                        mapping = {
                            "1": "claude-sonnet-4-6",
                            "2": "claude-opus-4-6-thinking",
                            "3": "gemini-3-flash",
                            "4": "gemini-2.5-pro",
                        }
                        if key in mapping:
                            chosen = mapping[key]
                            set_current_claude_model(chosen)
                            banner_message = f"Default Model Set: {chosen}"
                            banner_expires = time.time() + 3.0
                            current_view = VIEW_DASHBOARD
                        elif key in ("ESC", "0", "q", "m", "\n", "\r"):
                            current_view = VIEW_DASHBOARD

                    elif current_view == VIEW_DAEMON:
                        from gateway.daemon.macos_daemon import install_launchagent, uninstall_launchagent
                        if key == "1":
                            install_launchagent()
                            banner_message = "LaunchAgent Installed & Running"
                            banner_expires = time.time() + 3.0
                        elif key == "2":
                            uninstall_launchagent()
                            banner_message = "LaunchAgent Stopped & Removed"
                            banner_expires = time.time() + 3.0
                        elif key == "3":
                            uninstall_launchagent()
                            time.sleep(0.5)
                            install_launchagent()
                            banner_message = "LaunchAgent Restarted"
                            banner_expires = time.time() + 3.0
                        elif key in ("ESC", "0", "q", "d", "\n", "\r"):
                            current_view = VIEW_DASHBOARD

                    elif current_view == VIEW_HELP:
                        if key in ("ESC", "q", "?", "\n", "\r", "0"):
                            current_view = VIEW_DASHBOARD

            except (KeyboardInterrupt, EOFError):
                pass


if __name__ == "__main__":
    run_control_panel()
