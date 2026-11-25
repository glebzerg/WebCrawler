import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from math import isfinite
from urllib.parse import quote, urlsplit


_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")


def _normalize_path(path: str) -> str:
    def normalize_escape(match: re.Match[str]) -> str:
        escape = match[0]
        character = chr(int(escape[1:], 16))
        if character.isascii() and (character.isalnum() or character in "-._~"):
            return character
        return escape.upper()

    encoded = quote(path, safe="/:?[]@!$&'()*+,;=%")
    return _PERCENT_ESCAPE.sub(normalize_escape, encoded)


@dataclass(frozen=True)
class _Rule:
    pattern: str
    specificity: int
    allow: bool

    @classmethod
    def from_path(cls, path: str, *, allow: bool) -> "_Rule":
        normalized = _normalize_path(path)
        anchored = normalized.endswith("$")
        prefix = normalized[:-1] if anchored else normalized
        # In robots.txt only '*' and trailing '$' are special in a pattern.
        pattern = prefix.replace("[", "[[]").replace("?", "[?]")
        if not anchored:
            pattern += "*"
        return cls(pattern, len(normalized), allow)


@dataclass
class _AgentRules:
    paths: list[_Rule] = field(default_factory=list)
    crawl_delay: float = 0.0


class RobotsParser:
    """Read robots.txt rules supplied as text for one site."""

    def __init__(self, text: str) -> None:
        self._rules: dict[str, _AgentRules] = {}
        agents: set[str] = set()
        reading_rules = False

        for line in text.removeprefix("\ufeff").splitlines():
            name, _, value = line.partition("#")[0].partition(":")
            name = name.strip().lower()
            value = value.strip()

            if name == "user-agent" and value:
                if reading_rules:
                    agents.clear()
                    reading_rules = False
                agent = value.lower()
                agents.add(agent)
                if agent not in self._rules:
                    self._rules[agent] = _AgentRules()
            elif agents and name in {"allow", "disallow", "crawl-delay"}:
                reading_rules = True
                if name == "crawl-delay":
                    self._set_crawl_delay(agents, value)
                elif value:
                    rule = _Rule.from_path(value, allow=name == "allow")
                    for agent in agents:
                        self._rules[agent].paths.append(rule)

    def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        rules = self._rules_for(user_agent)
        if rules is None:
            return True

        parts = urlsplit(url)
        target = parts.path or "/"
        if parts.query or url.partition("#")[0].endswith("?"):
            target += f"?{parts.query}"
        target = _normalize_path(target)
        if target == "/robots.txt":
            return True

        best = max(
            (rule for rule in rules.paths if fnmatchcase(target, rule.pattern)),
            key=lambda rule: (rule.specificity, rule.allow),
            default=None,
        )
        return best is None or best.allow

    def get_crawl_delay(self, user_agent: str = "*") -> float:
        rules = self._rules_for(user_agent)
        return rules.crawl_delay if rules is not None else 0.0

    def _rules_for(self, user_agent: str) -> _AgentRules | None:
        products = user_agent.strip().split(maxsplit=1)
        token = products[0].split("/", 1)[0].lower() if products else ""
        return self._rules.get(token) or self._rules.get("*")

    def _set_crawl_delay(self, agents: set[str], value: str) -> None:
        try:
            delay = float(value)
        except ValueError:
            return
        if isfinite(delay) and delay >= 0:
            for agent in agents:
                rules = self._rules[agent]
                rules.crawl_delay = max(rules.crawl_delay, delay)
