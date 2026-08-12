import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Sequence, Tuple


PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ConfigError(ValueError):
    """Raised when application configuration is missing or invalid."""


def _strip_inline_comment(value: str) -> str:
    quote: Optional[str] = None
    result = []
    for index, character in enumerate(value):
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
        if character == "#" and quote is None:
            if index == 0 or value[index - 1].isspace():
                break
        result.append(character)
    return "".join(result).strip()


def read_env(path: Path) -> Dict[str, str]:
    path = Path(path)
    if not path.is_file():
        raise ConfigError("Configuration file not found: {}".format(path))

    values: Dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError("Invalid .env line {}".format(line_number))
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(raw_value)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _provider_names(raw: Sequence[str]) -> Tuple[str, ...]:
    names = []
    seen = set()
    for item in raw:
        name = item.strip().lower()
        if not name or not PROVIDER_NAME.fullmatch(name):
            raise ConfigError("Invalid enabled provider name")
        if name not in seen:
            names.append(name)
            seen.add(name)
    if not names:
        raise ConfigError("At least one provider must be enabled")
    return tuple(names)


@dataclass(frozen=True)
class AppSettings:
    values: Mapping[str, str] = field(repr=False)
    enabled_providers: Tuple[str, ...]
    report_directory: Path = Path("reports")

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "enabled_providers", tuple(self.enabled_providers))

    @classmethod
    def load(
        cls,
        path: Path,
        enabled: Optional[Sequence[str]] = None,
        output_dir: Optional[Path] = None,
    ) -> "AppSettings":
        return cls.from_values(read_env(path), enabled=enabled, output_dir=output_dir)

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, str],
        enabled: Optional[Sequence[str]] = None,
        output_dir: Optional[Path] = None,
    ) -> "AppSettings":
        if enabled is None:
            if "ENABLED_PROVIDERS" in values:
                raw_names = values["ENABLED_PROVIDERS"].split(",")
            else:
                raw_names = ["atgpay"]
        else:
            raw_names = list(enabled)

        if output_dir is None:
            report_text = values.get(
                "REPORT_DIRECTORY",
                values.get("ATG_REPORT_DIRECTORY", "reports"),
            )
            if not report_text.strip():
                raise ConfigError("REPORT_DIRECTORY cannot be empty")
            report_directory = Path(report_text)
        else:
            report_directory = Path(output_dir)
            if not str(report_directory).strip():
                raise ConfigError("Report directory cannot be empty")

        return cls(
            values=values,
            enabled_providers=_provider_names(raw_names),
            report_directory=report_directory,
        )
