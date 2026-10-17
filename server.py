"""
server.py — 手账本的 HTTP 服务（纯标准库）

跑起来就有两样东西：
  · 一个能翻看的本子页面（你翻，模型写）
  · 一组给模型调用的接口（POST /api/notebook）

配上模型接口后，页面上可以点一下让它自己写一条；不配也能用，
只是本子得由你自己的 agent 通过接口来写。
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

import notebook_store as nb
import prompts

_HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(_HERE, "static")
CONFIG_FILE = os.path.join(_HERE, "config.json")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
LLM_TIMEOUT = 60
MAX_BODY = 64 * 1024          # 请求体上限：手滑灌进来一整本书也别把服务拖住


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return ""


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception as e:
            print("config.json 读不动（按空配置起）: %s" % e)
    llm = dict(cfg.get("llm") or {})
    llm["base_url"] = _env("LLM_BASE_URL") or llm.get("base_url") or ""
    llm["api_key"] = _env("LLM_API_KEY") or llm.get("api_key") or ""
    llm["model"] = _env("LLM_MODEL") or llm.get("model") or ""
    cfg["llm"] = llm
    cfg["host"] = _env("NOTEBOOK_HOST") or cfg.get("host") or DEFAULT_HOST
    try:
        cfg["port"] = int(_env("NOTEBOOK_PORT") or cfg.get("port") or DEFAULT_PORT)
    except ValueError:
        cfg["port"] = DEFAULT_PORT
    return cfg


CONFIG = load_config()


def _llm_ready():
    l = CONFIG.get("llm") or {}
    return bool(l.get("base_url") and l.get("api_key") and l.get("model"))


def _post_json(url, payload, key, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- 写操作

def _page_digest(view):
    """把当前页读成几行字，喂给模型看（它自己写的本子，得知道自己写到哪了）。"""
    pages = view.get("pages") or []
    cur = view.get("current") or 0
    page = pages[cur] if 0 <= cur < len(pages) else None
    lines = []
    for e in (page or {}).get("elements", []):
        t = e.get("type")
        if t == "text":
            lines.append(("- " if e.get("struck") else "") + str(e.get("text") or ""))
        elif t == "image":
            lines.append("[贴了一张图]")
        elif t == "svg":
            lines.append("[贴了一幅画]")
        elif t == "tape":
            lines.append("[贴了一条胶带]")
    return "\n".join(lines) or "（这页还空着）"


def do_action(body):
    """把一次工具调用落到本子上。返回本子视图 + ok/msg（+ 翻页/换本标记）。"""
    action = str(body.get("action") or "").strip()
    before = nb.current_book_id()
    if action == "write":
        ok, msg, turned = nb.write(body.get("text"), x=body.get("x"), y=body.get("y"),
                                   rotation=body.get("rotation"),
                                   font_size=body.get("font_size"), color=body.get("color"))
    elif action == "strike":
        ok, msg = nb.strike(body.get("ref"))
        turned = False
    elif action == "unstrike":
        ok, msg = nb.unstrike(body.get("ref"))
        turned = False
    elif action == "add_image":
        ok, msg, turned = nb.add_image(body.get("src"), x=body.get("x"), y=body.get("y"),
                                       rotation=body.get("rotation"), w=body.get("w"), h=body.get("h"))
    elif action == "add_svg":
        ok, msg, turned = nb.add_svg(body.get("svg"), x=body.get("x"), y=body.get("y"),
                                     rotation=body.get("rotation"), w=body.get("w"), h=body.get("h"))
    elif action == "tape":
        ok, msg, turned = nb.tape(body.get("ref"), x=body.get("x"), y=body.get("y"),
                                  rotation=body.get("rotation"), w=body.get("w"),
                                  h=body.get("h"), color=body.get("color"))
    elif action == "new_page":
        page_no, rolled = nb.new_page()
        ok, msg, turned = True, "已翻到第 %d 页" % page_no, rolled
    elif action == "goto_page":
        ok, msg, turned = True, nb.goto_page(body.get("n")), False
    elif action == "new_book":
        ok, msg, turned = nb.new_book(body.get("title"), body.get("pages"),
                                      body.get("w"), body.get("h"))
    elif action == "goto_book":
        ok, msg, turned = nb.goto_book(body.get("ref"))
    elif action == "cover":
        ok, msg, turned = nb.cover(body.get("svg"), body.get("image"))
    elif action == "list_books":
        bs = nb.books()
        ok, turned = True, False
        msg = "本子共 %d 本：" % len(bs) + "；".join(
            "第 %d 本《%s》写到第 %d 页" % (b.get("no"), b.get("title"), b.get("page_count"))
            for b in bs)
    else:
        raise ValueError("未知 action: %s" % (action or "(空)"))
    data = nb.view()
    data["ok"] = ok
    data["msg"] = msg
    data["turned_page"] = turned
    data["new_book"] = nb.current_book_id() != before
    return data


def ask_model():
    """让模型看一眼本子，决定写不写。返回 (ok, said, msg)。"""
    if not _llm_ready():
        return False, "", "没配模型接口（config.json 的 llm 一节）"
    l = CONFIG["llm"]
    url = l["base_url"].rstrip("/") + "/chat/completions"
    view = nb.view()
    messages = [
        {"role": "system", "content": prompts.KNOCK_SYSTEM},
        {"role": "user", "content": "本子现在这一页上写着：\n%s\n\n%s"
                                    % (_page_digest(view), prompts.KNOCK_USER)},
    ]
    payload = {"model": l["model"], "messages": messages,
               "tools": [prompts.NOTEBOOK_TOOL], "temperature": 0.9}
    r = _post_json(url, payload, l["api_key"], LLM_TIMEOUT)
    msg = ((r.get("choices") or [{}])[0].get("message")) or {}
    calls = msg.get("tool_calls") or []
    if not calls:
        # 没写也是正常结果：本子又不是作业
        return True, "", str(msg.get("content") or "").strip()[:200]
    try:
        args = json.loads(calls[0].get("function", {}).get("arguments") or "{}")
    except Exception:
        return False, "", "模型给的工具参数不是合法 JSON"
    if not isinstance(args, dict):
        return False, "", "模型给的工具参数不是对象"
    said = str(args.pop("say", "") or "")
    data = do_action(args)
    return True, said, str(data.get("msg") or "")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "notebook"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                b = f.read()
        except OSError:
            return self._json({"ok": False, "error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError("body too large")
        try:
            d = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}
        return d if isinstance(d, dict) else {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        if u.path == "/notebook.js":
            return self._file(os.path.join(STATIC_DIR, "notebook.js"), "text/javascript; charset=utf-8")
        if u.path == "/api/notebook":
            return self._json(nb.view((q.get("book") or [None])[0]))
        if u.path == "/api/notebook/books":
            return self._json({"books": nb.books()})
        if u.path == "/api/config":
            return self._json({"llm": _llm_ready(), "books": len(nb.books())})
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body()
        except ValueError:
            return self._json({"ok": False, "error": "body too large"}, 413)
        try:
            if u.path == "/api/notebook":
                return self._json(do_action(body))

            if u.path == "/api/notebook/bookmark":
                act = str(body.get("action") or "").strip()
                if act == "add":
                    ok, msg, _ = nb.add_bookmark(body.get("page"), book_id=body.get("book"))
                elif act == "remove":
                    ok, msg, _ = nb.remove_bookmark(body.get("ref"), book_id=body.get("book"))
                else:
                    return self._json({"ok": False, "error": "action 只能是 add / remove"}, 400)
                data = nb.view(body.get("book"))
                data["ok"] = ok
                data["msg"] = msg
                return self._json(data)

            if u.path == "/api/notebook/knock":
                ok, said, msg = ask_model()
                data = nb.view()
                data["ok"] = ok
                data["said"] = said
                data["msg"] = msg
                return self._json(data, 200 if ok else 400)

        except ValueError as e:
            return self._json({"ok": False, "error": str(e)[:200]}, 400)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)[:200]}, 500)
        return self._json({"ok": False, "error": "not found"}, 404)


def main():
    host, port = CONFIG["host"], CONFIG["port"]
    srv = ThreadingHTTPServer((host, port), Handler)
    print("手账本：http://%s:%d" % (host, port))
    if not _llm_ready():
        print("没配模型接口：本子得由你自己的 agent 通过 POST /api/notebook 来写。")
        print("想让它自己写，把 config.example.json 复制成 config.json，填上 llm 一节。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停了")


if __name__ == "__main__":
    main()
