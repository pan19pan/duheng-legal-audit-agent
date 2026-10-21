"""本地服务：零第三方依赖，python app.py 直接跑。

刻意不用 FastAPI/Flask：开源项目的第一道门槛是「能不能一键跑起来」。
使用者 clone 下来只需要一个 python，不需要 pip install。
"""

import argparse
import html
import io
import json
import os
import re
import sys
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import engine, llm  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB, **kwargs)

    def log_message(self, fmt, *args):
        if os.environ.get("DUHENG_VERBOSE"):
            sys.stdout.write("[http] " + fmt % args + "\n")

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _extract_docx(self):
        """接收原始 .docx 字节流，返回提取出的纯文本，供前端填入输入框。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad request"}, 400)
        if length <= 0 or length > 10 * 1024 * 1024:
            return self._json({"error": "文件为空或超过 10MB 上限"}, 400)
        data = self.rfile.read(length)
        text = _docx_text(data)
        if text is None:
            return self._json({"error": "无法解析：这不是有效的 .docx 文件"}, 400)
        if len(text) < 4:
            return self._json({"error": "文档里没有提取到正文文字"}, 400)
        return self._json({"text": text})

    def do_GET(self):
        if self.path.startswith("/api/meta"):
            cases = _load(os.path.join(ROOT, "data", "sample_cases.json"))
            rs = engine.ruleset()
            return self._json({
                "product": "度衡 DuHeng",
                "ruleset": rs["version"],
                "updated": rs["updated"],
                "rule_count": len(rs["rules"]),
                "layers": rs["layers"],
                "behaviors": len(engine.behaviors()),
                "llm_available": llm.available(),
                "samples": [{"case_id": c["case_id"], "title": c["title"], "tag": c["tag"]} for c in cases],
            })
        if self.path.startswith("/api/sample/"):
            case_id = self.path.rsplit("/", 1)[-1]
            for c in _load(os.path.join(ROOT, "data", "sample_cases.json")):
                if c["case_id"] == case_id:
                    return self._json(c)
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/extract_docx"):
            return self._extract_docx()
        if not self.path.startswith("/api/audit"):
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "invalid json"}, 400)

        text = (payload.get("facts_text") or "").strip()
        if len(text) < 4:
            return self._json({"error": "请输入案件事实，或点击上方示例案件"}, 400)

        result = engine.audit(
            {
                "case_id": payload.get("case_id") or None,
                "facts_text": text,
                "structured": payload.get("structured") or {},
                "review_date": payload.get("review_date") or None,
            },
            use_llm=bool(payload.get("use_llm", True)),
        )
        return self._json(result)


def _docx_text(data):
    """从 .docx 字节流提取正文文本（docx 即 zip，用标准库解析，零依赖）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError):
        return None
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t").replace("<w:br/>", "\n")
    text = re.sub(r"<[^>]+>", "", xml)
    lines = [ln.strip() for ln in html.unescape(text).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="度衡 DuHeng 本地演示服务")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    mode = "LLM 增强已启用" if llm.available() else "纯规则模式（未配置 LLM 密钥，功能完整可用）"
    print("=" * 54)
    print("  度衡 DuHeng · 行政处罚合规审查助手")
    print("  http://127.0.0.1:%d" % args.port)
    print("  规则集 v%s / %d 条审查要点 / %s" % (
        engine.ruleset()["version"], len(engine.ruleset()["rules"]), mode))
    print("  Ctrl+C 停止")
    print("=" * 54)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
