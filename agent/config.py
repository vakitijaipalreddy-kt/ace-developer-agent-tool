r"""Configuration loading for the ACE Developer Agent.

Credentials are read from a config file *outside* the repository so that a
token can never be committed by accident. Environment variables win over the
config file, which lets CI or a shell session override anything.

Default config path:  %USERPROFILE%\.ace-pr-agent\config.json
Override with:        ACE_PR_AGENT_CONFIG=<path to json>
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV = "ACE_PR_AGENT_CONFIG"
DEFAULT_CONFIG_DIR = Path.home() / ".ace-pr-agent"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"

TEMPLATE = {
    "jira_base_url": "https://kroger.atlassian.net",
    "jira_email": "you@kroger.com",
    "jira_token": "<Atlassian API token from id.atlassian.com/manage-profile/security/api-tokens>",
    "repo_path": "C:/repos/pos-ace-control-code",
    "base_branch": "main",
    "github_token": "",
    "claude_model": "claude-opus-5",
    "branch_template": "{key}",
    "commit_template": "{key} - {summary}",
    "post_pr_link_to_jira": True,
    "theme": "dark",
}


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    jira_base_url: str = "https://kroger.atlassian.net"
    jira_email: str = ""
    jira_token: str = ""
    repo_path: str = ""
    base_branch: str = "main"
    github_token: str = ""
    claude_model: str = "claude-opus-5"
    branch_template: str = "{key}"
    commit_template: str = "{key} - {summary}"
    post_pr_link_to_jira: bool = True
    theme: str = "dark"
    source: str = field(default="defaults")

    @property
    def jira_api(self) -> str:
        return self.jira_base_url.rstrip("/") + "/rest/api/3"

    def issue_url(self, key: str) -> str:
        return self.jira_base_url.rstrip("/") + "/browse/" + key

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty list means OK."""
        problems = []
        if not self.jira_email:
            problems.append("jira_email is not set")
        if not self.jira_token or self.jira_token.startswith("<"):
            problems.append("jira_token is not set")
        if not self.repo_path:
            problems.append("repo_path is not set")
        elif not (Path(self.repo_path) / ".git").exists():
            problems.append("repo_path is not a git repository: " + self.repo_path)
        return problems


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    return Path(override) if override else DEFAULT_CONFIG_PATH


# Keys that are persisted to the config file, in display order.
SAVED_KEYS = (
    "jira_base_url",
    "jira_email",
    "jira_token",
    "repo_path",
    "base_branch",
    "github_token",
    "claude_model",
    "branch_template",
    "commit_template",
    "post_pr_link_to_jira",
    "theme",
)

# Which environment variable shadows each field, for warning the user when a
# value they type in the form will not actually take effect.
ENV_FOR_KEY = {
    "jira_base_url": "JIRA_BASE_URL",
    "jira_email": "JIRA_EMAIL",
    "jira_token": "JIRA_TOKEN",
    "repo_path": "ACE_REPO_PATH",
    "base_branch": "ACE_BASE_BRANCH",
    "github_token": "GH_TOKEN",
    "claude_model": "ACE_CLAUDE_MODEL",
}


def shadowing_env_vars(keys=SAVED_KEYS) -> dict[str, str]:
    """Return {field: env var} for fields currently overridden by the env."""
    found = {}
    for key in keys:
        env = ENV_FOR_KEY.get(key)
        if env and os.environ.get(env) not in (None, ""):
            found[key] = env
    return found


def save(values: dict, path: Path | None = None) -> Path:
    """Write the given field values to the config file.

    Only known keys are written, and any unrelated keys already in the file
    are preserved so hand-added settings are not lost.
    """
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    for key in SAVED_KEYS:
        if key in values:
            existing[key] = values[key]

    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return path


def write_template(path: Path | None = None) -> Path:
    """Create a starter config file. Never overwrites an existing one."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(TEMPLATE, indent=2), encoding="utf-8")
    return path


def load() -> Config:
    path = config_path()
    data: dict = {}
    source = "environment only"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            source = str(path)
        except json.JSONDecodeError as exc:
            raise ConfigError("{} is not valid JSON: {}".format(path, exc)) from exc

    def pick(key: str, env: str, default):
        value = os.environ.get(env)
        if value not in (None, ""):
            return value
        return data.get(key, default)

    return Config(
        jira_base_url=pick("jira_base_url", "JIRA_BASE_URL",
                           "https://kroger.atlassian.net"),
        jira_email=pick("jira_email", "JIRA_EMAIL", ""),
        jira_token=pick("jira_token", "JIRA_TOKEN", ""),
        repo_path=pick("repo_path", "ACE_REPO_PATH", os.getcwd()),
        base_branch=pick("base_branch", "ACE_BASE_BRANCH", "main"),
        github_token=pick("github_token", "GH_TOKEN", ""),
        claude_model=pick("claude_model", "ACE_CLAUDE_MODEL", "claude-opus-5"),
        branch_template=data.get("branch_template", "{key}"),
        commit_template=data.get("commit_template", "{key} - {summary}"),
        post_pr_link_to_jira=bool(data.get("post_pr_link_to_jira", True)),
        theme=data.get("theme", "dark"),
        source=source,
    )
