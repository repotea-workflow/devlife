#!/usr/bin/env python3
"""Apply Bot-specific branding to the pinned upstream nanobot source tree."""

from __future__ import annotations

import json
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Any


ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace(path: str, replacements: dict[str, str]) -> None:
    text = read(path)
    original = text
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text == original:
        raise RuntimeError(f"no replacements applied to {path}")
    write(path, text)


def patch_runtime_paths() -> None:
    replace(
        "Dockerfile",
        {
            "RUN useradd -m -u 1000 -s /bin/bash nanobot && \\\n"
            "    mkdir -p /home/nanobot/.nanobot && \\\n"
            "    chown -R nanobot:nanobot /home/nanobot /app/.venv": (
                "RUN useradd -m -u 1000 -s /bin/bash bot && \\\n"
                "    mkdir -p /data /home/bot /home/nanobot && \\\n"
                "    ln -sfn /data /home/bot/.nanobot && \\\n"
                "    ln -sfn /data /home/nanobot/.nanobot && \\\n"
                "    chown -h bot:bot /home/bot/.nanobot /home/nanobot/.nanobot && \\\n"
                "    chown -R bot:bot /data /home/bot /home/nanobot /app/.venv && \\\n"
                "    printf '%s\\n' '#!/bin/sh' 'exec nanobot \"$@\"' > /usr/local/bin/bot && \\\n"
                "    chmod +x /usr/local/bin/bot"
            ),
            "ENV HOME=/home/nanobot": "ENV HOME=/home/bot",
            "non-root nanobot user": "non-root bot user",
            "non-root\n# nanobot user": "non-root\n# bot user",
        },
    )
    replace(
        "entrypoint.sh",
        {
            'dir="$HOME/.nanobot"': 'dir="${NANOBOT_DATA_DIR:-/data}"',
            '    mkdir -p "$dir" || echo "[entrypoint] warning: mkdir $dir failed"': (
                '    mkdir -p "$dir" || echo "[entrypoint] warning: mkdir $dir failed"'
            ),
            '    chown -R nanobot:nanobot "$dir" 2>/dev/null || echo "[entrypoint] warning: chown $dir failed"\n'
            '    if setpriv --reuid=nanobot --regid=nanobot --init-groups true 2>/dev/null; then\n'
            '        echo "[entrypoint] dropping privileges to nanobot via setpriv"\n'
            '        exec setpriv --reuid=nanobot --regid=nanobot --init-groups nanobot "$@"\n'
            "    fi": (
                '    mkdir -p "$dir" /home/bot /home/nanobot || echo "[entrypoint] warning: data dir setup failed"\n'
                '    ln -sfn "$dir" /home/bot/.nanobot 2>/dev/null || echo "[entrypoint] warning: linking /home/bot/.nanobot failed"\n'
                '    ln -sfn "$dir" /home/nanobot/.nanobot 2>/dev/null || echo "[entrypoint] warning: linking legacy data path failed"\n'
                '    chown -h bot:bot /home/bot/.nanobot /home/nanobot/.nanobot 2>/dev/null || true\n'
                '    chown -R bot:bot "$dir" 2>/dev/null || echo "[entrypoint] warning: chown $dir failed"\n'
                '    if setpriv --reuid=bot --regid=bot --init-groups true 2>/dev/null; then\n'
                '        echo "[entrypoint] dropping privileges to bot via setpriv"\n'
                '        exec setpriv --reuid=bot --regid=bot --init-groups bot "$@"\n'
                "    fi"
            ),
            "re-exec as nanobot": "re-exec as bot",
            "non-root nanobot user": "non-root bot user",
            "dropping privileges to nanobot": "dropping privileges to bot",
            "exec nanobot \"$@\"": "exec bot \"$@\"",
            "Host:   sudo chown -R 1000:1000 ~/.nanobot": "Host:   sudo chown -R 1000:1000 /data",
        },
    )


def patch_cli_defaults() -> None:
    replace(
        "nanobot/__init__.py",
        {'__logo__ = "🐈"': '__logo__ = ""'},
    )
    replace(
        "pyproject.toml",
        {
            '[project.scripts]\nnanobot = "nanobot.cli.entry:main"': (
                '[project.scripts]\n'
                'nanobot = "nanobot.cli.entry:main"\n'
                'bot = "nanobot.cli.entry:main"'
            ),
        },
    )
    replace(
        "nanobot/config/loader.py",
        {
            '    return Path.home() / ".nanobot" / "config.json"': (
                '    configured = os.environ.get("BOT_CONFIG") or os.environ.get("NANOBOT_CONFIG")\n'
                '    return Path(configured).expanduser() if configured else Path("/data/config.json")'
            ),
        },
    )
    replace(
        "nanobot/config/paths.py",
        {
            'Path.home() / ".nanobot" / "workspace"': 'Path("/data/workspace")',
            'Path.home() / ".nanobot" / "history" / "cli_history"': 'Path("/data/history/cli_history")',
            'Path.home() / ".nanobot" / "sessions"': 'Path("/data/sessions")',
        },
    )
    replace(
        "nanobot/config/schema.py",
        {
            'workspace: str = "~/.nanobot/workspace"': 'workspace: str = "/data/workspace"',
            'bot_name: str = "nanobot"': 'bot_name: str = "Bot"',
            'bot_icon: str = "🐈"': 'bot_icon: str = ""',
        },
    )
    replace(
        "nanobot/cli/commands.py",
        {
            'name="nanobot"': 'name="bot"',
            'help=f"{__logo__} nanobot - Personal AI Assistant"': 'help="Bot - Personal AI Assistant"',
            "Run `nanobot` without a subcommand to start the terminal agent.": "Run `bot` without a subcommand to start the terminal agent.",
            "Use `nanobot agent --help` for agent options.": "Use `bot agent --help` for agent options.",
            'console.print(f"{__logo__} nanobot v{__version__}")': 'console.print(f"Bot v{__version__}")',
            '"""Show nanobot status."""': '"""Show Bot status."""',
            'console.print(f"{__logo__} nanobot Status\\n")': 'console.print("Bot Status\\n")',
        },
    )
    replace(
        "nanobot/cli/terminal.py",
        {
            'console.print(f"[cyan]{__logo__} nanobot[/cyan]")': 'console.print("[cyan]Bot[/cyan]")',
            'target.print(f"[cyan]{__logo__} nanobot[/cyan]")': 'target.print("[cyan]Bot[/cyan]")',
        },
    )
    replace(
        "nanobot/cli/gateway_runtime.py",
        {
            'console.print(f"{__logo__} Starting nanobot gateway version {__version__} on port {port}...")': (
                'console.print(f"Starting Bot gateway version {__version__} on port {port}...")'
            ),
        },
    )
    replace(
        "nanobot/cli/onboard.py",
        {
            'body.add_row(f"{__logo__} [bold {_UI_TEXT}]nanobot[/] [{_UI_MUTED}]v{__version__}[/]")': (
                'body.add_row(f"[bold {_UI_TEXT}]Bot[/] [{_UI_MUTED}]v{__version__}[/]")'
            ),
            "This lets the browser UI at http://127.0.0.1:8765 connect to nanobot.": (
                "This lets the browser UI at http://127.0.0.1:8765 connect to Bot."
            ),
        },
    )
    replace(
        "nanobot/cli/agent.py",
        {
            'help="Show nanobot runtime logs during chat"': 'help="Show Bot runtime logs during chat"',
            "Use `nanobot agent --classic` only if you want the old prompt.": (
                "Use `bot agent --classic` only if you want the old prompt."
            ),
            "_icon = runtime_config.agents.defaults.bot_icon or __logo__": "_icon = runtime_config.agents.defaults.bot_icon",
            'f"{_icon} Interactive mode [bold blue]({_model})[/bold blue]{_preset_tag} "': (
                'f"{_icon + \' \' if _icon else \'\'}Interactive mode [bold blue]({_model})[/bold blue]{_preset_tag} "'
            ),
        },
    )
    replace(
        "nanobot/cli/runtime_config.py",
        {
            'return f\'nanobot status --config "{config_path}"\'': 'return f\'bot status --config "{config_path}"\'',
            "WebUI: run [cyan]nanobot webui": "WebUI: run [cyan]bot webui",
            "CLI:   run [cyan]nanobot onboard --wizard": "CLI:   run [cyan]bot onboard --wizard",
        },
    )


def patch_workspace_templates() -> None:
    replace(
        "nanobot/templates/SOUL.md",
        {"I am nanobot 🐈, a personal AI assistant.": "I am Bot, a personal AI assistant."},
    )
    replace(
        "nanobot/templates/legacy/SOUL.md",
        {"I am nanobot 🐈, a personal AI assistant.": "I am Bot, a personal AI assistant."},
    )
    replace(
        "nanobot/templates/USER.md",
        {"nanobot's behavior": "Bot's behavior"},
    )
    replace(
        "nanobot/templates/memory/MEMORY.md",
        {"updated by nanobot": "updated by Bot"},
    )
    replace(
        "nanobot/templates/HEARTBEAT.md",
        {
            "your nanobot agent": "your Bot agent",
            "When nanobot gateway starts": "When Bot gateway starts",
            "`nanobot gateway`": "`bot gateway`",
        },
    )
    replace(
        "nanobot/templates/AGENTS.md",
        {
            "`nanobot cron`": "`bot cron`",
            "nanobot sends": "Bot sends",
            "`nanobot gateway`": "`bot gateway`",
        },
    )
    replace(
        "nanobot/templates/agent/identity.md",
        {"Nanobot's agent workspace is at:": "Bot's agent workspace is at:"},
    )
    replace(
        "nanobot/templates/agent/subagent_system.md",
        {"Nanobot's agent workspace:": "Bot's agent workspace:"},
    )
    replace(
        "nanobot/templates/agent/cron_reminder.md",
        {"nanobot delivers": "Bot delivers"},
    )
    replace(
        "nanobot/templates/prompts/README.md",
        {"nanobot's default memory behavior": "Bot's default memory behavior"},
    )


def brand_text(value: str) -> str:
    if "http://nanobot.wiki" in value or "https://nanobot.wiki" in value:
        return value
    value = value.replace("nanobot WebUI", "Bot WebUI")
    value = value.replace("nanobot web UI", "Bot web UI")
    value = value.replace("nanobot's", "Bot's")
    value = value.replace("Nanobot's", "Bot's")
    value = re.sub(r"\bnanobot\b", "Bot", value)
    value = re.sub(r"\bNanobot\b", "Bot", value)
    value = value.replace("~/.nanobot", "/data")
    value = value.replace("/.nanobot/workspace", "/workspace")
    return value


def patch_json_strings(path: Path) -> None:
    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return brand_text(node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        return node

    data = json.loads(path.read_text(encoding="utf-8"))
    patched = walk(data)
    path.write_text(json.dumps(patched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_png(path: Path, size: int, *, maskable: bool = False) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    background = (37, 99, 235, 255)
    foreground = (255, 255, 255, 255)
    margin = int(size * (0.16 if maskable else 0.08))
    bar = max(2, size // 13)
    radius = max(2, size // 10)
    glyph_left = int(size * 0.34)
    glyph_top = int(size * 0.24)
    glyph_bottom = int(size * 0.76)
    glyph_mid = size // 2
    glyph_right = int(size * 0.64)

    rows: list[bytes] = []
    center = (size - 1) / 2
    round_radius = center - margin
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            distance = ((x - center) ** 2 + (y - center) ** 2) ** 0.5
            pixel = background if distance <= round_radius else (0, 0, 0, 0)
            in_stem = glyph_left <= x < glyph_left + bar and glyph_top <= y <= glyph_bottom
            in_top = glyph_top <= y < glyph_top + bar and glyph_left <= x <= glyph_right - radius
            in_mid = glyph_mid - bar // 2 <= y < glyph_mid + bar // 2 and glyph_left <= x <= glyph_right - radius
            in_bottom = glyph_bottom - bar <= y <= glyph_bottom and glyph_left <= x <= glyph_right - radius
            top_loop = (
                glyph_mid - radius <= y <= glyph_mid
                and glyph_left + bar <= x <= glyph_right
                and abs(((x - (glyph_right - radius)) ** 2 + (y - (glyph_top + radius)) ** 2) ** 0.5 - radius) <= bar
            )
            bottom_loop = (
                glyph_mid <= y <= glyph_bottom
                and glyph_left + bar <= x <= glyph_right
                and abs(((x - (glyph_right - radius)) ** 2 + (y - (glyph_bottom - radius)) ** 2) ** 0.5 - radius) <= bar
            )
            if pixel[3] and (in_stem or in_top or in_mid or in_bottom or top_loop or bottom_loop):
                pixel = foreground
            row.extend(pixel)
        rows.append(bytes(row))

    data = b"".join(rows)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(data, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def patch_brand_assets() -> None:
    brand_dir = ROOT / "webui/public/brand"
    mark = """<svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect x="6" y="6" width="52" height="52" rx="16" fill="#2563EB"/>
<path d="M23 17H36.5C43.1 17 47 20.5 47 26C47 29.2 45.5 31.7 42.8 33.1C46.2 34.4 48 37.1 48 40.8C48 46.7 43.8 50 36.6 50H23V17ZM35.4 30.4C38.5 30.4 40.2 29 40.2 26.5C40.2 24.1 38.5 22.7 35.4 22.7H29.8V30.4H35.4ZM36.2 44.3C39.5 44.3 41.3 42.8 41.3 40.1C41.3 37.4 39.5 36 36.2 36H29.8V44.3H36.2Z" fill="white"/>
</svg>
"""
    wordmark = """<svg width="112" height="32" viewBox="0 0 112 32" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect x="1" y="1" width="30" height="30" rx="8" fill="#2563EB"/>
<path d="M11 8.5H17.9C21.3 8.5 23.3 10.3 23.3 13.1C23.3 14.7 22.5 16 21.2 16.7C22.9 17.4 23.8 18.8 23.8 20.7C23.8 23.7 21.6 25.5 17.9 25.5H11V8.5ZM17.3 15.4C18.9 15.4 19.8 14.7 19.8 13.4C19.8 12.2 18.9 11.5 17.3 11.5H14.5V15.4H17.3ZM17.7 22.5C19.4 22.5 20.3 21.8 20.3 20.4C20.3 19 19.4 18.3 17.7 18.3H14.5V22.5H17.7Z" fill="white"/>
<path d="M42.1 23.3C38 23.3 35.1 20.4 35.1 16.2C35.1 12 38 9.1 42.1 9.1C46.2 9.1 49.1 12 49.1 16.2C49.1 20.4 46.2 23.3 42.1 23.3ZM42.1 20C44 20 45.3 18.5 45.3 16.2C45.3 13.9 44 12.4 42.1 12.4C40.2 12.4 38.9 13.9 38.9 16.2C38.9 18.5 40.2 20 42.1 20ZM56.9 23.3C52.8 23.3 49.9 20.4 49.9 16.2C49.9 12 52.8 9.1 56.9 9.1C61 9.1 63.9 12 63.9 16.2C63.9 20.4 61 23.3 56.9 23.3ZM56.9 20C58.8 20 60.1 18.5 60.1 16.2C60.1 13.9 58.8 12.4 56.9 12.4C55 12.4 53.7 13.9 53.7 16.2C53.7 18.5 55 20 56.9 20ZM72.8 23C69.5 23 67.8 21.3 67.8 18.1V12.6H65.5V9.4H67.8V5.9H71.5V9.4H75.2V12.6H71.5V17.9C71.5 19.1 72.1 19.8 73.2 19.8C73.9 19.8 74.6 19.6 75.1 19.3L76.1 22.2C75.2 22.9 74.1 23 72.8 23Z" fill="#111827"/>
</svg>
"""
    (brand_dir / "nanobot_mark.svg").write_text(mark, encoding="utf-8")
    (brand_dir / "nanobot_wordmark.svg").write_text(wordmark, encoding="utf-8")
    write_png(brand_dir / "nanobot_favicon_32.png", 32)
    write_png(brand_dir / "nanobot_apple_touch.png", 180)
    write_png(brand_dir / "nanobot_icon_192.png", 192)
    write_png(brand_dir / "nanobot_icon_512.png", 512)
    write_png(brand_dir / "nanobot_icon_maskable.png", 512, maskable=True)


def patch_dashboard() -> None:
    replace(
        "webui/index.html",
        {
            "nanobot web UI — chat with your nanobot workspace.": "Bot web UI — chat with your Bot workspace.",
            'content="nanobot"': 'content="Bot"',
            "<title>nanobot</title>": "<title>Bot</title>",
            "Loading nanobot…": "Loading Bot…",
            "正在加载 nanobot…": "正在加载 Bot…",
            "nanobot Web UI —— 与你的 nanobot 工作区对话。": "Bot Web UI —— 与你的 Bot 工作区对话。",
            "正在載入 nanobot…": "正在載入 Bot…",
            "nanobot Web UI —— 與你的 nanobot 工作區對話。": "Bot Web UI —— 與你的 Bot 工作區對話。",
            "Chargement de nanobot…": "Chargement de Bot…",
            "Interface web nanobot — discutez avec votre espace de travail nanobot.": "Interface web Bot — discutez avec votre espace de travail Bot.",
            "nanobot を読み込み中…": "Bot を読み込み中…",
            "nanobot Web UI — nanobot ワークスペースと会話します。": "Bot Web UI — Bot ワークスペースと会話します。",
            "nanobot 불러오는 중…": "Bot 불러오는 중…",
            "nanobot 웹 UI — nanobot 작업공간과 대화하세요.": "Bot 웹 UI — Bot 작업공간과 대화하세요.",
            "Cargando nanobot…": "Cargando Bot…",
            "Interfaz web de nanobot: conversa con tu espacio de trabajo de nanobot.": "Interfaz web de Bot: conversa con tu espacio de trabajo de Bot.",
            "Carregando nanobot…": "Carregando Bot…",
            "Interface web do nanobot — converse com o seu espaço de trabalho do nanobot.": "Interface web do Bot — converse com o seu espaço de trabalho do Bot.",
            "Đang tải nanobot…": "Đang tải Bot…",
            "Giao diện web nanobot — trò chuyện với không gian làm việc nanobot của bạn.": "Giao diện web Bot — trò chuyện với không gian làm việc Bot của bạn.",
            "Memuat nanobot…": "Memuat Bot…",
            "UI web nanobot — ngobrol dengan ruang kerja nanobot Anda.": "UI web Bot — ngobrol dengan ruang kerja Bot Anda.",
        },
    )
    patch_json_strings(ROOT / "webui/public/manifest.json")
    for path in sorted((ROOT / "webui/src/i18n/locales").glob("*/common.json")):
        patch_json_strings(path)
    for path in sorted((ROOT / "nanobot/channels").glob("*/webui/locales/*.json")):
        patch_json_strings(path)
    replace(
        "nanobot/channels/websocket/webui/index.ts",
        {'displayName: "nanobot WebUI"': 'displayName: "Bot WebUI"'},
    )
    replace(
        "nanobot/channels/linear/webui/manifest.ts",
        {
            'developer: { name: "nanobot" }': 'developer: { name: "Bot" }',
            'client_name: "nanobot Agent"': 'client_name: "Bot Agent"',
        },
    )
    replace(
        "nanobot/channels/linear/webui/LinearMemberAccess.tsx",
        {'"Allow {{name}} to use nanobot"': '"Allow {{name}} to use Bot"'},
    )
    patch_brand_assets()


def main() -> None:
    patch_runtime_paths()
    patch_cli_defaults()
    patch_workspace_templates()
    patch_dashboard()


if __name__ == "__main__":
    main()
