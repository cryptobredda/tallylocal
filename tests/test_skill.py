"""The skill artifact is part of the product: it must exist and install."""
import json


def test_skill_text_has_frontmatter_and_tools():
    from app.skill import skill_text
    text = skill_text()
    assert text.startswith('---')
    assert 'name: tally' in text
    assert 'get_context' in text
    assert 'raw_record_locked' in text


def test_install_writes_a_skill_file(tmp_path):
    from app.skill import install
    destination = install('hermes', str(tmp_path / 'skills' / 'tally' / 'SKILL.md'))
    assert destination.exists()
    assert 'Tally Local' in destination.read_text(encoding='utf-8')


def test_install_creates_parent_directories(tmp_path):
    from app.skill import install
    destination = install('claude', str(tmp_path / 'deep' / 'nested' / 'SKILL.md'))
    assert destination.parent.is_dir()


def test_mcp_config_is_paste_ready(tmp_path):
    from app.skill import mcp_config
    config = mcp_config(data_dir=str(tmp_path), actor='test-agent')
    server = config['mcpServers']['tally']
    assert server['args'] == ['-m', 'app.mcp_server']
    assert server['env']['DATA_DIR'] == str(tmp_path)
    assert server['env']['TALLY_ACTOR'] == 'test-agent'
    json.dumps(config)  # must be serialisable
