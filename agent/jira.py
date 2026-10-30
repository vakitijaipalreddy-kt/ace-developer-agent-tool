"""Minimal Jira Cloud REST v3 client: read an issue, post a comment."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

# Corporate networks often terminate TLS with their own CA. Python trusts
# certifi's bundle, which does not contain that CA, so HTTPS fails here even
# though PowerShell (which uses the Windows certificate store) works fine.
# `truststore` makes Python use the OS store too. It is optional: without it
# the tool still works on networks that do not intercept TLS.
try:
    import truststore

    truststore.inject_into_ssl()
    SYSTEM_CERTS = True
except Exception:
    SYSTEM_CERTS = False

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


class JiraError(RuntimeError):
    pass


@dataclass
class Issue:
    key: str
    summary: str = ""
    description: str = ""
    status: str = ""
    priority: str = ""
    issue_type: str = ""
    assignee: str = ""
    reporter: str = ""
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    url: str = ""

    def as_prompt_context(self, max_comments: int = 10) -> str:
        """Render the issue as plain text for the Claude prompt."""
        parts = [
            "Jira issue: " + self.key,
            "URL: " + self.url,
            "Type: {}    Status: {}    Priority: {}".format(
                self.issue_type, self.status, self.priority),
            "Reporter: {}    Assignee: {}".format(self.reporter, self.assignee),
        ]
        if self.labels:
            parts.append("Labels: " + ", ".join(self.labels))
        if self.components:
            parts.append("Components: " + ", ".join(self.components))
        parts.append("\nSummary:\n" + self.summary)
        parts.append("\nDescription:\n" + (self.description or "(no description)"))
        if self.comments:
            parts.append("\nComments (oldest first):")
            for c in self.comments[-max_comments:]:
                parts.append("  --- {} on {} ---".format(c["author"], c["created"][:10]))
                for line in c["body"].splitlines():
                    parts.append("  " + line)
        return "\n".join(parts)


def normalize_key(raw: str) -> str:
    """Accept 'ace-24308', ' ACE-24308 ', or a browse URL; return 'ACE-24308'."""
    raw = (raw or "").strip()
    if "/browse/" in raw:
        raw = raw.rsplit("/browse/", 1)[1]
    raw = raw.split("?")[0].strip().strip("/").upper()
    if not ISSUE_KEY_RE.match(raw):
        raise JiraError(
            "'{}' is not a valid Jira issue key (expected e.g. ACE-24308)".format(raw)
        )
    return raw


def adf_to_text(node: Any, depth: int = 0) -> str:
    """Flatten Atlassian Document Format into readable plain text.

    Jira v3 returns rich text as a nested ADF document. We only need something
    a language model can read, so this keeps paragraphs, lists, headings, code
    blocks and link targets, and ignores pure styling.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n, depth) for n in node)
    if not isinstance(node, dict):
        return str(node)

    ntype = node.get("type", "")
    content = node.get("content", [])

    if ntype == "text":
        text = node.get("text", "")
        for mark in node.get("marks", []):
            if mark.get("type") == "link":
                href = mark.get("attrs", {}).get("href", "")
                if href and href != text:
                    text = text + " <" + href + ">"
            elif mark.get("type") == "code":
                text = "`" + text + "`"
        return text
    if ntype == "hardBreak":
        return "\n"
    if ntype == "paragraph":
        return adf_to_text(content, depth) + "\n"
    if ntype == "heading":
        level = node.get("attrs", {}).get("level", 1)
        return "\n" + "#" * level + " " + adf_to_text(content, depth).strip() + "\n"
    if ntype in ("bulletList", "orderedList"):
        out = []
        for i, item in enumerate(content, 1):
            bullet = "- " if ntype == "bulletList" else "{}. ".format(i)
            body = adf_to_text(item, depth + 1).strip()
            pad = "  " * depth
            lines = body.splitlines() or [""]
            out.append(pad + bullet + lines[0])
            out.extend(pad + "  " + line for line in lines[1:])
        return "\n".join(out) + "\n"
    if ntype == "listItem":
        return adf_to_text(content, depth)
    if ntype == "codeBlock":
        lang = node.get("attrs", {}).get("language", "")
        return "\n```" + lang + "\n" + adf_to_text(content, depth) + "\n```\n"
    if ntype == "blockquote":
        inner = adf_to_text(content, depth).strip()
        return "\n".join("> " + line for line in inner.splitlines()) + "\n"
    if ntype == "rule":
        return "\n---\n"
    if ntype == "mention":
        return "@" + node.get("attrs", {}).get("text", "").lstrip("@")
    if ntype == "emoji":
        return node.get("attrs", {}).get("shortName", "")
    if ntype == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    if ntype == "tableRow":
        cells = [adf_to_text(c, depth).strip().replace("\n", " ") for c in content]
        return "| " + " | ".join(cells) + " |\n"
    if ntype in ("table", "tableCell", "tableHeader"):
        return adf_to_text(content, depth)
    if ntype in ("mediaSingle", "mediaGroup", "media"):
        return "[attachment]"
    return adf_to_text(content, depth)


def text_to_adf(text: str) -> dict:
    """Wrap plain text in a minimal ADF document for posting a comment."""
    paragraphs = []
    for block in text.split("\n"):
        if block.strip():
            paragraphs.append(
                {"type": "paragraph", "content": [{"type": "text", "text": block}]}
            )
        else:
            paragraphs.append({"type": "paragraph", "content": []})
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": paragraphs}


class JiraClient:
    def __init__(self, base_url: str, email: str, token: str, timeout: int = 30):
        self.api = base_url.rstrip("/") + "/rest/api/3"
        self.browse = base_url.rstrip("/") + "/browse"
        self.timeout = timeout
        pair = (email + ":" + token).encode("utf-8")
        self._headers = {
            "Authorization": "Basic " + base64.b64encode(pair).decode("ascii"),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self.api + path
        try:
            resp = requests.request(
                method, url, headers=self._headers, timeout=self.timeout, **kwargs
            )
        except requests.exceptions.SSLError as exc:
            raise JiraError(
                "TLS verification failed for {}.\n\n"
                "This usually means the network inspects HTTPS with a "
                "corporate certificate that Python does not trust.\n"
                "Fix it by letting Python use the Windows certificate store:\n"
                "    pip install truststore\n"
                "then restart the tool.{}\n\n"
                "Details: {}".format(
                    url,
                    "" if SYSTEM_CERTS else "  (truststore is not installed)",
                    exc,
                )
            ) from exc
        except requests.RequestException as exc:
            raise JiraError("Could not reach Jira at {}: {}".format(url, exc)) from exc

        if resp.status_code in (401, 403):
            raise JiraError(
                "Jira rejected the credentials (HTTP {}). "
                "Check jira_email and jira_token in your config.".format(
                    resp.status_code)
            )
        if resp.status_code == 404:
            raise JiraError(
                "Jira returned 404 for {} - issue missing or no access.".format(path)
            )
        if resp.status_code >= 400:
            raise JiraError(
                "Jira HTTP {} for {}: {}".format(resp.status_code, path,
                                                 resp.text[:400])
            )
        return resp

    def get_issue(self, key: str) -> Issue:
        key = normalize_key(key)
        resp = self._request(
            "GET",
            "/issue/" + key,
            params={
                "fields": "summary,description,status,priority,issuetype,assignee,"
                          "reporter,labels,components,comment",
            },
        )
        data = resp.json()
        f = data.get("fields", {}) or {}

        def name_of(obj, attr="name"):
            return (obj or {}).get(attr, "") if isinstance(obj, dict) else ""

        comments = []
        for c in ((f.get("comment") or {}).get("comments") or []):
            comments.append(
                {
                    "author": name_of(c.get("author"), "displayName") or "unknown",
                    "created": c.get("created", ""),
                    "body": adf_to_text(c.get("body")).strip(),
                }
            )

        resolved_key = data.get("key", key)
        return Issue(
            key=resolved_key,
            summary=f.get("summary", "") or "",
            description=adf_to_text(f.get("description")).strip(),
            status=name_of(f.get("status")),
            priority=name_of(f.get("priority")),
            issue_type=name_of(f.get("issuetype")),
            assignee=name_of(f.get("assignee"), "displayName"),
            reporter=name_of(f.get("reporter"), "displayName"),
            labels=list(f.get("labels") or []),
            components=[name_of(c) for c in (f.get("components") or [])],
            comments=comments,
            url=self.browse + "/" + resolved_key,
        )

    def add_comment(self, key: str, text: str) -> str:
        key = normalize_key(key)
        resp = self._request(
            "POST",
            "/issue/" + key + "/comment",
            data=json.dumps({"body": text_to_adf(text)}),
        )
        return resp.json().get("id", "")
