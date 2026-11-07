"""Install the Tally skill into an agent's skill directory, and print MCP config."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SKILL_PATH = Path(__file__).parent / 'skills' / 'tally' / 'SKILL.md'

TARGETS = {
    'hermes': Path.home() / '.hermes' / 'skills' / 'tally' / 'SKILL.md',
    'claude': Path.home() / '.claude' / 'skills' / 'tally' / 'SKILL.md',
    'cursor': Path.cwd() / '.cursor' / 'rules' / 'tally.mdc',
}


def skill_text() -> str:
    return SKILL_PATH.read_text(encoding='utf-8')


def install(target: str, path: str | None = None) -> Path:
    target = target.lower()
    if target not in TARGETS and not path:
        raise ValueError(f'Unknown target {target!r}. Use one of: {", ".join(TARGETS)}')
    destination = Path(path).expanduser() if path else TARGETS[target]
    if destination.is_dir():
        destination = destination / ('tally.mdc' if target == 'cursor' else 'SKILL.md')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(skill_text(), encoding='utf-8')
    return destination


def mcp_config(data_dir: str | None = None, actor: str = 'agent') -> dict:
    """A ready-to-paste MCP server entry pointing at this checkout/venv."""
    root = Path(__file__).resolve().parent.parent
    return {
        'mcpServers': {
            'tally': {
                'command': sys.executable,
                'args': ['-m', 'app.mcp_server'],
                'env': {
                    'DATA_DIR': os.path.abspath(data_dir or os.getenv('DATA_DIR') or root),
                    'TALLY_ACTOR': actor,
                },
            }
        }
    }


def mcp_config_text(**kwargs) -> str:
    return json.dumps(mcp_config(**kwargs), indent=2)
