"""`.env.example` должен документировать каждую переменную, которую читает config.py."""
import re
from pathlib import Path

from tiktok_obsidian import config

ROOT = Path(config.__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
CONFIG_SRC = Path(config.__file__).resolve()

# Имя переменной в вызове os.getenv("NAME", ...) / _int_env("NAME", ...) / _float_env("NAME", ...).
_READ = re.compile(r"(?:os\.getenv|_int_env|_float_env)\(\s*[\"']([A-Z0-9_]+)[\"']")
# Строка .env: NAME=... либо закомментированный пример "# NAME=...".
_DECL = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)

REQUIRED = ("TELEGRAM_TOKEN", "ALLOWED_USER_ID", "VAULT_ROOT")


def _names_read_by_config() -> set[str]:
    return set(_READ.findall(CONFIG_SRC.read_text(encoding="utf-8")))


def _names_declared_in_example() -> set[str]:
    return set(_DECL.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def test_env_example_covers_every_variable_config_reads():
    missing = _names_read_by_config() - _names_declared_in_example()
    assert not missing, f"нет в .env.example: {sorted(missing)}"


def test_required_variables_are_active_and_first():
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    active = [ln.split("=", 1)[0].strip() for ln in lines if _DECL.match(ln) and not ln.lstrip().startswith("#")]
    assert active[:3] == list(REQUIRED)
