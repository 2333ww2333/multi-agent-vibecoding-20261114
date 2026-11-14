"""网页抓取工具：HTML 转文本、限长、SSRF 防护。"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from .registry import E_TOOL_INVALID_ARGS, E_WEB_BLOCKED, E_WEB_FETCH, ToolError, ToolSpec

MAX_BYTES = 2_000_000
DEFAULT_UA = "Mozilla/5.0 (compatible; multi-agent/0.1; +https://example.local)"


class _TextExtractor(HTMLParser):
    """把 HTML 压成可读文本：跳过 script/style，块级标签换行。"""

    BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5",
                  "h6", "section", "article", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        return re.sub(r"\n{3,}", "\n\n", joined).strip()


def _assert_public(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError(E_TOOL_INVALID_ARGS, f"只支持 http/https，收到 {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ToolError(E_TOOL_INVALID_ARGS, f"URL 缺少主机名: {url}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ToolError(E_WEB_FETCH, f"域名解析失败: {host}（{exc}）") from exc

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ToolError(
                E_WEB_BLOCKED,
                f"拒绝访问内网/保留地址：{host} → {address}",
                hint="只能抓取公网 http/https 地址。",
            )


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """每一跳都重新做一次 SSRF 校验。

    urllib 默认会自动跟随 30x；如果只校验初始 URL，一个公网地址只要
    302 到 http://169.254.169.254/ 就能绕过防护。
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Any:
        _assert_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def web_fetch(ctx: Any, url: str, extract_mode: str = "text",
              max_chars: int = 8000) -> str:
    """抓取网页，默认把 HTML 转成纯文本。"""
    if not isinstance(url, str) or not url.strip():
        raise ToolError(E_TOOL_INVALID_ARGS, "url 不能为空")
    url = url.strip()
    _assert_public(url)

    if extract_mode not in {"text", "raw"}:
        raise ToolError(E_TOOL_INVALID_ARGS, "extract_mode 只能是 text 或 raw")

    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA,
                                                   "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    opener = urllib.request.build_opener(_PublicOnlyRedirectHandler())
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(MAX_BYTES)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise ToolError(E_WEB_FETCH, f"HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(E_WEB_FETCH, f"网络错误：{exc.reason}（{url}）") from exc
    except TimeoutError as exc:
        raise ToolError(E_WEB_FETCH, f"请求超时：{url}") from exc

    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1)
    try:
        html = raw.decode(charset, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")

    if extract_mode == "raw" or "html" not in content_type.lower():
        text = html
    else:
        parser = _TextExtractor()
        parser.feed(html)
        text = parser.text()

    limit = max(200, min(int(max_chars), 40_000))
    if len(text) > limit:
        text = text[:limit] + f"\n\n[... 已截断：原始 {len(text)} 字符 ...]"
    return f"URL: {url}\n\n{text}"


SPECS = [
    ToolSpec(
        name="web_fetch",
        description=(
            "抓取一个公网网页并返回正文文本（自动去标签）。"
            "查官方文档、博客、issue 时使用。"
            "只能访问公网地址；一次只抓一个网页，需要多页时派 searcher 子代理。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 http/https URL"},
                "extract_mode": {
                    "type": "string", "enum": ["text", "raw"],
                    "description": "text=去标签正文（默认），raw=原始 HTML", "default": "text",
                },
                "max_chars": {"type": "integer", "description": "最多返回字符数", "default": 8000},
            },
            "required": ["url"],
        },
        handler=web_fetch,
        risk="read",
    ),
]
