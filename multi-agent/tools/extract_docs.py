"""把下载的 HTML 文档转成纯文本，按行区间或按起止关键字截取片段。

用法:
  python tools/extract_docs.py <html文件> --lines 720 800
  python tools/extract_docs.py <html文件> --between "起始关键字" "结束关键字"
"""
import re
import sys


def to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", "", raw)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#x27;", "'"), ("&apos;", "'"), ("&amp;", "&")):
        raw = raw.replace(a, b)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n", raw)
    return raw


def main() -> None:
    path = sys.argv[1]
    raw = open(path, encoding="utf-8", errors="ignore").read()
    if sys.argv[2] == "--lines":
        start, end = int(sys.argv[3]), int(sys.argv[4])
        chunk = "\n".join(raw.splitlines()[start - 1:end])
        print(to_text(chunk).strip())
    else:
        start_kw, end_kw = sys.argv[3], sys.argv[4]
        text = to_text(raw)
        i = text.find(start_kw)
        if i < 0:
            print(f"[未找到起始关键字] {start_kw}")
            return
        j = text.find(end_kw, i + len(start_kw))
        print((text[i:] if j < 0 else text[i:j]).strip())


if __name__ == "__main__":
    main()
