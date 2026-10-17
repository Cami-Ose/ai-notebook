"""
notebook_store.py — 手账本数据层（纯标准库）

一本给模型用的手写本：一页一张纸，模型像写字一样往纸上记条目、贴图、贴 SVG，
写上去就固定——元素带 x/y/rotation，按写入次序烙在纸上，不是能拖动的卡片。
想划就划（strike，保留字迹当痕迹），想开新页就开新页，写不下了自动换页。

一本写满（到 pages_per_book，默认 24 页）自动归档、开新的一本；每本有自己的纸面尺寸、
封面（模型画的 SVG，或一张图片地址）、书签（人在页面上手动夹，只在本子里有效）。
书目落 notebook/index.json，一本一个文件 notebook/<book_id>.json。

一本的结构：{
  "id": "b_...",
  "pages": [ { "id","created_ts","elements": [元素] } ],
  "current": <当前写作页在 pages 里的下标>,
  "bookmarks": [ { "id","page","created_ts" } ]
}
书目（index.json）：{
  "books": [ { "id","title","created_ts","closed_ts","pages_per_book","size":{"w","h"},
               "cover": {"kind":"svg"|"image","data":...} | null } ],
  "current": "<当前写作本 id>"
}
元素（"写上去就固定" = 坐标/角度随元素存，渲染即按此摆位，不做拖动交互）：
  text  {"id","type":"text","text","x","y","rotation","font_size","color","struck","struck_ts","created_ts"}
  image {"id","type":"image","src","x","y","rotation","w","h","created_ts"}
  svg   {"id","type":"svg","svg","x","y","rotation","w","h","created_ts"}
  tape  {"id","type":"tape","x","y","rotation","w","h","color","opacity","created_ts"}
        —— 胶带：盖在别的元素上（给 ref 贴时按那条的宽度自动量长），**不参与流式占位**

对外接口（全部线程安全）：
  view(book_id=None)        → 一本的全量视图 + 书目摘要，供前端渲染 / LLM 回显
  books()                   → 书目列表（每本页数、书签数、是不是当前写作本）
  current_text()            → 当前页元素的文本摘要（上下文注入 / LLM 回显用）
  write / add_image / add_svg / tape → 当前页落元素（写满翻页；本子写满自动换新本）
  strike(ref) / unstrike(ref)        → 按元素 id 精确或文字包含，划掉 / 取消划掉
  new_page()                → 开新的一页（已是本子最后一页 → 换新本）
  goto_page(n)              → 切当前页（1 基，clamp）
  new_book(...)             → 归档当前本、开一本新的（可带标题/页数/纸面尺寸）
  goto_book(ref)            → 切当前写作本（id / 序号 1 基 / 标题包含）
  cover(svg=, image=)       → 给本子画/换封面（擦掉就都不传）
  add_bookmark(page) / remove_bookmark(ref) → 书签（人在页面上手动夹）
"""

import json
import logging
import math
import os
import random
import threading
import time

log = logging.getLogger("notebook")

_HERE = os.path.dirname(os.path.abspath(__file__))
NOTEBOOK_DIR = os.path.join(_HERE, "notebook")
INDEX_FILE = os.path.join(NOTEBOOK_DIR, "index.json")

# 纸面默认尺寸（px）：前端渲染按这个坐标系绝对定位；每本可以在书目里带自己的 size
DEF_PAGE_W = 900
DEF_PAGE_H = 600
PAGE_BOTTOM_MARGIN = 40   # 纸面底部留白：写到这条线就算「写满了」→ 换新页
# 流式布局参数：不指定坐标时，元素像纸面写字一样从左往右排，一行放不下换行，页满翻页
MARGIN_X = 24        # 纸面左边距
MARGIN_Y = 50        # 纸面顶部留白（第一行 y）
LINE_GAP = 26        # 行距
EL_GAP = 14          # 行内元素间距
TEXT_MAX_W = 420     # 文本渲染最大宽度（book.html .sn-text max-width）
# 默认外观（暖纸本子：纸上写字用墨色）
DEF_FONT_SIZE = 17
DEF_COLOR = "#3a2a1a"
DEF_IMG_W = 180
DEF_IMG_H = 130
DEF_SVG_W = 160
DEF_SVG_H = 110
DEF_TAPE_W = 140     # 胶带默认长/宽（给 ref 贴时长度按那条元素算）
DEF_TAPE_H = 26
TAPE_COLORS = ("#e6d3a3", "#cfe0e8", "#f0d5d8", "#d9e6cf", "#e8dcc0")  # 米黄/淡蓝/淡粉/淡绿/米白
DEF_TAPE_OPACITY = 0.55
DEF_PAGES_PER_BOOK = 24   # 一本几页

# 所有「读-改-写」在锁内，防止多任务并发写本子打架（可重入：内部函数会互相调用）
_lock = threading.RLock()
_id_seq = 0


def _new_id(prefix):
    """生成元素/页/本子 id：<prefix>_<epoch秒>_<序号>；序号用模块内递增计数器，避免删除后撞号。"""
    global _id_seq
    _id_seq += 1
    return f"{prefix}_{int(time.time())}_{_id_seq}"


def _safe_bid(bid):
    """本子 id 只允许字母数字下划线（它会拼进文件名，挡掉 ../ 这类）。非法返回 None。"""
    bid = str(bid or "").strip()
    if not bid or not all(c.isalnum() or c == "_" for c in bid):
        return None
    return bid


# ---------------------------------------------------------------- 书目 / 本子文件

def _ensure_dirs():
    os.makedirs(NOTEBOOK_DIR, exist_ok=True)


def _book_file(bid):
    return os.path.join(NOTEBOOK_DIR, "%s.json" % bid)


def _write_json_atomic(path, data):
    """原子写（.tmp + os.replace），ensure_ascii=False 保留中文。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_index(idx):
    _ensure_dirs()
    _write_json_atomic(INDEX_FILE, idx)


def _write_book_file(bid, data):
    _ensure_dirs()
    _write_json_atomic(_book_file(bid), data)


def _read_book_file(bid):
    """读一本；文件不存在 / 损坏 → 容错返回空本子。"""
    try:
        with open(_book_file(bid), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("pages"), list):
            if not isinstance(d.get("bookmarks"), list):
                d["bookmarks"] = []
            return d
    except Exception as e:
        log.warning("本子 %s 读取失败，容错返回空本子: %s", bid, e)
    return {"id": bid, "pages": [], "current": 0, "bookmarks": []}


def _meta(idx, bid):
    """书目里找一本的元信息条目（引用）；找不到返回 None。"""
    for b in idx.get("books") or []:
        if b.get("id") == bid:
            return b
    return None


def _book_size(m):
    size = (m or {}).get("size") or {}
    try:
        w = int(size.get("w") or DEF_PAGE_W)
        h = int(size.get("h") or DEF_PAGE_H)
    except (TypeError, ValueError):
        w, h = DEF_PAGE_W, DEF_PAGE_H
    return max(200, min(4000, w)), max(200, min(4000, h))


def _pages_per_book(m):
    try:
        n = int((m or {}).get("pages_per_book") or DEF_PAGES_PER_BOOK)
    except (TypeError, ValueError):
        n = DEF_PAGES_PER_BOOK
    return max(1, min(500, n))


def _rebuild_index_from_files():
    """index.json 丢了但本子文件还在：按文件名重建书目。"""
    ids = sorted(
        f[:-5] for f in os.listdir(NOTEBOOK_DIR)
        if f.endswith(".json") and not f.endswith(".tmp") and f != "index.json"
    )
    books = []
    for i, bid in enumerate(ids, 1):
        books.append({
            "id": bid, "title": "第 %d 本" % i, "created_ts": None, "closed_ts": None,
            "pages_per_book": DEF_PAGES_PER_BOOK,
            "size": {"w": DEF_PAGE_W, "h": DEF_PAGE_H}, "cover": None,
        })
    if not books:
        return None
    idx = {"books": books, "current": books[-1]["id"]}
    save_index(idx)
    log.warning("notebook/index.json 缺失，已按 %d 个本子文件重建书目", len(books))
    return idx


def load_index():
    """读书目；没有就按本子文件重建，再没有就开一本新的空本子。"""
    _ensure_dirs()
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                idx = json.load(f)
            if isinstance(idx, dict) and isinstance(idx.get("books"), list) and idx["books"]:
                if not _meta(idx, idx.get("current")):
                    idx["current"] = idx["books"][-1]["id"]
                return idx
        except Exception as e:
            log.warning("notebook/index.json 读取失败: %s", e)
    rebuilt = _rebuild_index_from_files()
    if rebuilt is not None:
        return rebuilt
    return _bootstrap_index()


def _bootstrap_index():
    """首次运行：开一本新的空本子。"""
    bid = _new_id("b")
    idx = {
        "books": [{
            "id": bid,
            "title": "第一本",
            "created_ts": int(time.time()),
            "closed_ts": None,
            "pages_per_book": DEF_PAGES_PER_BOOK,
            "size": {"w": DEF_PAGE_W, "h": DEF_PAGE_H},
            "cover": None,
        }],
        "current": bid,
    }
    _write_book_file(bid, {"id": bid, "pages": [], "current": 0, "bookmarks": []})
    save_index(idx)
    return idx


def _create_book(idx, title=None, pages_per_book=None, size=None):
    """在书目里加一本新的空本子并切为当前写作本，返回它的元信息条目。"""
    bid = _new_id("b")
    n = len(idx.get("books") or []) + 1
    m = {
        "id": bid,
        "title": str(title or "").strip() or ("第 %d 本" % n),
        "created_ts": int(time.time()),
        "closed_ts": None,
        "pages_per_book": _pages_per_book({"pages_per_book": pages_per_book}),
        "size": {"w": _book_size({"size": size})[0], "h": _book_size({"size": size})[1]},
        "cover": None,
    }
    idx.setdefault("books", []).append(m)
    idx["current"] = bid
    _write_book_file(bid, {"id": bid, "pages": [], "current": 0, "bookmarks": []})
    save_index(idx)
    return m


def _current_book_id(idx=None):
    idx = idx or load_index()
    bid = idx.get("current")
    if not _meta(idx, bid):
        bid = idx["books"][-1]["id"]
        idx["current"] = bid
        save_index(idx)
    return bid


def current_book_id():
    """当前写作本 id（给调用方判断"这一轮有没有换本"）。"""
    with _lock:
        return _current_book_id()


def _load_book_with_meta(bid):
    """读一本并挂上三个下划线键（不落盘）：_book_id / _pages_per_book / _page_w / _page_h。"""
    idx = load_index()
    m = _meta(idx, bid) or {}
    d = _read_book_file(bid)
    d["_book_id"] = bid
    d["_pages_per_book"] = _pages_per_book(m)
    d["_page_w"], d["_page_h"] = _book_size(m)
    return d


def load():
    """读当前写作本（挂下划线键，见 _load_book_with_meta）。"""
    with _lock:
        return _load_book_with_meta(_current_book_id())


def save(data):
    """原子写回它自己那本（下划线键不落盘）。"""
    bid = _safe_bid(data.get("_book_id"))
    if not bid:
        log.warning("save: 没有 _book_id，拒绝写入")
        return
    clean = {k: v for k, v in data.items() if not str(k).startswith("_")}
    _write_book_file(bid, clean)


# ---------------------------------------------------------------- 页 / 元素定位

def _ensure_page(data):
    """确保至少有一页；当前页下标越界时修正回最后一页。返回当前页 dict（引用）。"""
    pages = data["pages"]
    if not pages:
        pages.append({"id": _new_id("p"), "created_ts": int(time.time()), "elements": []})
    cur = data.get("current") or 0
    if not isinstance(cur, int) or cur < 0:
        cur = 0
    if cur >= len(pages):
        cur = len(pages) - 1
    data["current"] = cur
    return pages[cur]


def _new_page_internal(data):
    """追加一页空白页并把当前页切过去（new_page 与自动翻页共用）。返回新页 dict。"""
    pages = data["pages"]
    if not isinstance(pages, list):
        pages = []
        data["pages"] = pages
    pages.append({"id": _new_id("p"), "created_ts": int(time.time()), "elements": []})
    data["current"] = len(pages) - 1
    return pages[-1]


def _roll_over(data):
    """当前本写满 → 归档它（补 closed_ts）、开一本新的（继承页数上限和纸面尺寸），
    返回新本的 data（带下划线键）。调用方之后 save(data) 落的就是新本。"""
    old_bid = _safe_bid(data.get("_book_id"))
    idx = load_index()
    m = _meta(idx, old_bid)
    if m is not None and not m.get("closed_ts"):
        m["closed_ts"] = int(time.time())
        save_index(idx)
    nb = _create_book(
        idx,
        pages_per_book=data.get("_pages_per_book"),
        size={"w": data.get("_page_w"), "h": data.get("_page_h")},
    )
    log.info("手账本：上一本写满了，开了一本新的（%s）", nb["id"])
    return _load_book_with_meta(nb["id"])


def _place(data, x, y, w, h):
    """定位落位：返回 (data, page, px, py, turned)。
    x 指定 → 照写（不翻页）；x 未指定（自动流式落位）→ 当前页放不下就翻新页，
    已经是本子最后一页（到 pages_per_book）→ 归档换新本（data 会换成新本的）。"""
    if x is None:
        page = _ensure_page(data)
        page_h = data.get("_page_h") or DEF_PAGE_H
        avail = page_h - PAGE_BOTTOM_MARGIN - MARGIN_Y   # 一页能用的高度
        turned = False
        while True:
            px, py = _flow_spot(page["elements"], w, h, data.get("_page_w") or DEF_PAGE_W)
            if py + h <= page_h - PAGE_BOTTOM_MARGIN:
                break
            if h > avail:
                # 这一条自己就比一页还高，翻多少页都放不下 → 认了，写在这一页上
                # （不这么收口的话，会一直翻页/换本，转不出来）
                break
            if len(data["pages"]) >= (data.get("_pages_per_book") or DEF_PAGES_PER_BOOK):
                data = _roll_over(data)
                page = _ensure_page(data)
            else:
                page = _new_page_internal(data)
            turned = True
        return data, page, px, py, turned
    return data, _ensure_page(data), int(x), int(y or 0), False


def _fill_pct(elements, page_w=DEF_PAGE_W, page_h=DEF_PAGE_H):
    """当前页写满程度 0~100（按流式布局当前行底部计），给上下文注入/回执用「还剩多少」。"""
    if not elements:
        return 0
    x = MARGIN_X
    y = MARGIN_Y
    row_h = 0
    for el in elements:
        if el.get("type") == "tape":      # 胶带不占位
            continue
        ew = _est_width(el)
        eh = _est_height(el)
        if x > MARGIN_X and x + ew > page_w - MARGIN_X:
            x = MARGIN_X
            y += row_h + LINE_GAP
            row_h = 0
        row_h = max(row_h, eh)
        x += ew + EL_GAP
    used = y + row_h - MARGIN_Y
    total = page_h - PAGE_BOTTOM_MARGIN - MARGIN_Y
    return min(100, int(used / total * 100))


def _text_dims(text, fs):
    """估算 text 元素渲染宽/高（对齐 book.html：全角字宽≈字号、半角≈0.55字号、每字 0.5px 字距，
    超 TEXT_MAX_W 强制折行；行高 = 字号 × 1.5）。"""
    total = 0.0
    for ch in text:
        if ord(ch) > 0x7F:
            total += fs + 0.5        # 全角：字宽≈字号 + 字距
        else:
            total += fs * 0.55 + 0.5 # 半角
    w = min(TEXT_MAX_W, max(fs, total))
    rows = max(1, math.ceil(total / TEXT_MAX_W))
    return w, rows * fs * 1.5


def _est_width(el):
    """估算元素在纸面上的宽度（px），流式布局用来判断换行。"""
    t = el.get("type")
    if t == "text":
        fs = el.get("font_size") or DEF_FONT_SIZE
        return _text_dims(str(el.get("text") or ""), fs)[0]
    if t == "image":
        return el.get("w") or DEF_IMG_W
    if t == "tape":
        return el.get("w") or DEF_TAPE_W
    return el.get("w") or DEF_SVG_W


def _est_height(el):
    """估算元素在纸面上的高度（px），流式布局用来避让/换行。"""
    t = el.get("type")
    if t == "text":
        fs = el.get("font_size") or DEF_FONT_SIZE
        return _text_dims(str(el.get("text") or ""), fs)[1]
    if t == "image":
        return el.get("h") or DEF_IMG_H
    if t == "tape":
        return el.get("h") or DEF_TAPE_H
    return el.get("h") or DEF_SVG_H


def _flow_spot(elements, w, h, page_w=DEF_PAGE_W):
    """流式重放布局，返回下一元素应放的位置 (x, y)：
    从左往右排，一行放不下（超 page_w - 右边距）就换到下一行（y += 行高 + 行距）。
    胶带（tape）是盖上去的，不占位、不参与重放。"""
    x = MARGIN_X
    y = MARGIN_Y
    row_h = 0
    for el in elements:
        if el.get("type") == "tape":
            continue
        ew = _est_width(el)
        eh = _est_height(el)
        if x > MARGIN_X and x + ew > page_w - MARGIN_X:
            x = MARGIN_X
            y += row_h + LINE_GAP
            row_h = 0
        row_h = max(row_h, eh)
        x += ew + EL_GAP
    if x > MARGIN_X and x + w > page_w - MARGIN_X:
        x = MARGIN_X
        y += row_h + LINE_GAP
    return (x, y)


def _rot(value):
    """收敛 rotation：数字直接用；不指定/非法给一个轻微手写歪斜（-4°~4°），模拟随手写。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(random.randint(-4, 4))


def _find_element(page, ref):
    """按元素 id 精确 或 文字包含（text 元素）找元素；找不到返回 (None, None)。"""
    ref = str(ref or "").strip()
    if not ref:
        return None, None
    for el in page["elements"]:
        if str(el.get("id") or "") == ref:
            return el, "id"
    for el in page["elements"]:
        if el.get("type") == "text" and ref in str(el.get("text") or ""):
            return el, "text"
    return None, None


# ---------------------------------------------------------------- 读视图

def _shelf_locked(idx):
    """书目摘要（调用方已持锁）：每本页数、书签数、是不是当前写作本。"""
    out = []
    for i, b in enumerate(idx.get("books") or [], 1):
        bid = b.get("id")
        d = _read_book_file(bid)
        w, h = _book_size(b)
        out.append({
            "id": bid,
            "no": i,
            "title": b.get("title") or ("第 %d 本" % i),
            "cover": b.get("cover"),
            "size": {"w": w, "h": h},
            "pages_per_book": _pages_per_book(b),
            "page_count": len(d.get("pages") or []),
            "bookmark_count": len(d.get("bookmarks") or []),
            "created_ts": b.get("created_ts"),
            "closed_ts": b.get("closed_ts"),
            "current": bid == idx.get("current"),
        })
    return out


def books():
    """书目列表（给前端书架 / list_books 工具）。"""
    with _lock:
        return _shelf_locked(load_index())


def view(book_id=None):
    """一本的全量视图：{"pages","current","page_count","bookmarks","book","shelf"}。
    每页附 fill_pct（0~100，写满程度），供注入/回执告诉模型「这页还有多少空位」。
    book_id 不传 / 认不出来 → 当前写作本。老的三个键（pages/current/page_count）形状不变。"""
    with _lock:
        idx = load_index()
        bid = _safe_bid(book_id)
        if not bid or not _meta(idx, bid):
            bid = _current_book_id(idx)
        m = _meta(idx, bid) or {}
        data = _read_book_file(bid)
        _ensure_page(data)
        page_w, page_h = _book_size(m)
        out_pages = []
        for p in data["pages"]:
            pc = dict(p)
            pc["fill_pct"] = _fill_pct(pc.get("elements") or [], page_w, page_h)
            out_pages.append(pc)
        shelf = _shelf_locked(idx)
        no = next((s["no"] for s in shelf if s["id"] == bid), 1)
        return {
            "pages": out_pages,
            "current": data["current"],
            "page_count": len(data["pages"]),
            "bookmarks": data.get("bookmarks") or [],
            "book": {
                "id": bid,
                "no": no,
                "total": len(shelf),
                "title": m.get("title") or "",
                "cover": m.get("cover"),
                "size": {"w": page_w, "h": page_h},
                "pages_per_book": _pages_per_book(m),
                "created_ts": m.get("created_ts"),
                "closed_ts": m.get("closed_ts"),
                "current": bid == idx.get("current"),
            },
            "shelf": shelf,
        }


def current_text():
    """当前页元素 → 文本摘要列表（上下文注入 / LLM 回显用），每元素一行、带序号。"""
    with _lock:
        data = load()
        page = _ensure_page(data)
        els = page["elements"]
    lines = []
    for i, el in enumerate(els, 1):
        t = el.get("type")
        if t == "text":
            s = str(el.get("text") or "")
        elif t == "image":
            s = "[图] " + str(el.get("src") or "")
        elif t == "svg":
            s = "[SVG] " + (str(el.get("svg") or "")[:80])
        elif t == "tape":
            s = "[贴了条胶带]"
        else:
            s = "[?]"
        if el.get("struck"):
            s += "（已划掉）"
        lines.append(f"{i}. {s}")
    return lines


# ---------------------------------------------------------------- 写元素

def write(text, x=None, y=None, rotation=None, font_size=None, color=None):
    """当前页写一条文字：strip；空文本返回 (False, msg, turned)。
    坐标/角度不指定自动落下一空位 + 轻微歪斜；font_size/color 不指定用默认手写墨色。
    自动落位时若当前页已写满，翻新页；已是本子最后一页则换新本，返回 turned=True。"""
    text = str(text or "").strip()
    if not text:
        return False, "空字写不了", False
    fs = font_size if isinstance(font_size, (int, float)) and not isinstance(font_size, bool) else DEF_FONT_SIZE
    fs = max(10, min(48, int(fs)))
    with _lock:
        data = load()
        tw, th = _text_dims(text, fs)
        data, page, px, py, turned = _place(data, x, y, tw, th)
        el = {
            "id": _new_id("e"),
            "type": "text",
            "text": text,
            "x": px,
            "y": py,
            "rotation": _rot(rotation),
            "font_size": fs,
            "color": str(color or DEF_COLOR),
            "struck": False,
            "struck_ts": None,
            "created_ts": int(time.time()),
        }
        page["elements"].append(el)
        save(data)
        return True, el["id"], turned


def add_image(src, x=None, y=None, rotation=None, w=None, h=None):
    """当前页贴一张图：src 必填（URL）。不指定坐标自动落位，写满翻页/换本（turned=True）。"""
    src = str(src or "").strip()
    if not src:
        return False, "没给图地址", False
    w = w if isinstance(w, (int, float)) and not isinstance(w, bool) else DEF_IMG_W
    h = h if isinstance(h, (int, float)) and not isinstance(h, bool) else DEF_IMG_H
    with _lock:
        data = load()
        data, page, px, py, turned = _place(data, x, y, int(w), int(h))
        el = {
            "id": _new_id("e"),
            "type": "image",
            "src": src,
            "x": px,
            "y": py,
            "rotation": _rot(rotation),
            "w": int(w),
            "h": int(h),
            "created_ts": int(time.time()),
        }
        page["elements"].append(el)
        save(data)
        return True, el["id"], turned


def add_svg(svg, x=None, y=None, rotation=None, w=None, h=None):
    """当前页贴一段 SVG：svg 必填（手写 SVG 代码）。不指定坐标自动落位，写满翻页/换本。"""
    svg = str(svg or "").strip()
    if not svg:
        return False, "没给 SVG", False
    w = w if isinstance(w, (int, float)) and not isinstance(w, bool) else DEF_SVG_W
    h = h if isinstance(h, (int, float)) and not isinstance(h, bool) else DEF_SVG_H
    with _lock:
        data = load()
        data, page, px, py, turned = _place(data, x, y, int(w), int(h))
        el = {
            "id": _new_id("e"),
            "type": "svg",
            "svg": svg,
            "x": px,
            "y": py,
            "rotation": _rot(rotation),
            "w": int(w),
            "h": int(h),
            "created_ts": int(time.time()),
        }
        page["elements"].append(el)
        save(data)
        return True, el["id"], turned


def tape(ref=None, x=None, y=None, rotation=None, w=None, h=None, color=None, opacity=None):
    """当前页贴一条胶带：给 ref 就盖在那条元素上（长度按那条的宽度自动算），
    也能直接给 x/y。胶带不占位（不把后面的东西挤走），纯装饰、盖在上层。
    颜色不指定就随机挑一条胶带色；opacity 默认 0.55（半透明，底下的字还看得见）。"""
    with _lock:
        data = load()
        page = _ensure_page(data)
        hh = h if isinstance(h, (int, float)) and not isinstance(h, bool) else DEF_TAPE_H
        if ref is not None and str(ref).strip():
            target, how = _find_element(page, ref)
            if target is None:
                return False, "没找到要贴的那条", False
            ew = _est_width(target)
            eh = _est_height(target)
            tw = w if isinstance(w, (int, float)) and not isinstance(w, bool) else (ew + 24)
            tx = int(target.get("x") or MARGIN_X) - 12
            ty = int(target.get("y") or MARGIN_Y) + int(eh / 2) - int(hh / 2)
        else:
            if x is None:
                return False, "胶带要贴在哪：给 ref（贴在那条上）或 x/y", False
            tw = w if isinstance(w, (int, float)) and not isinstance(w, bool) else DEF_TAPE_W
            tx, ty = int(x), int(y or 0)
        op = opacity if isinstance(opacity, (int, float)) and not isinstance(opacity, bool) else DEF_TAPE_OPACITY
        el = {
            "id": _new_id("e"),
            "type": "tape",
            "x": tx,
            "y": ty,
            "rotation": _rot(rotation if rotation is not None else random.uniform(-6, 6)),
            "w": int(tw),
            "h": int(hh),
            "color": str(color or random.choice(TAPE_COLORS)),
            "opacity": max(0.1, min(1.0, float(op))),
            "created_ts": int(time.time()),
        }
        page["elements"].append(el)
        save(data)
        return True, el["id"], False


def strike(ref):
    """划掉当前页一个元素：按 id 精确或文字包含，置 struck=true + struck_ts。保留字迹，不删。"""
    with _lock:
        data = load()
        page = _ensure_page(data)
        el, how = _find_element(page, ref)
        if el is None:
            return False, "没找到这条"
        el["struck"] = True
        el["struck_ts"] = int(time.time())
        save(data)
        return True, "划掉了"


def unstrike(ref):
    """取消划掉：恢复 struck=false。"""
    with _lock:
        data = load()
        page = _ensure_page(data)
        el, how = _find_element(page, ref)
        if el is None:
            return False, "没找到这条"
        el["struck"] = False
        el["struck_ts"] = None
        save(data)
        return True, "恢复了"


# ---------------------------------------------------------------- 翻页 / 换本

def new_page():
    """开新的一页。返回 (页码 1 基, 是否换了新本)。
    已经是本子最后一页（到 pages_per_book）→ 归档换新本，返回新本的第 1 页。"""
    with _lock:
        data = load()
        if len(data["pages"]) >= (data.get("_pages_per_book") or DEF_PAGES_PER_BOOK):
            data = _roll_over(data)
            _ensure_page(data)
            save(data)
            return 1, True
        _new_page_internal(data)
        save(data)
        return len(data["pages"]), False


def goto_page(n):
    """切当前页（1 基，clamp 到有效范围）。返回页码（1 基）。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 1
    with _lock:
        data = load()
        _ensure_page(data)
        pages = data["pages"]
        idx = max(0, min(len(pages) - 1, n - 1))
        data["current"] = idx
        save(data)
        return idx + 1


def new_book(title=None, pages=None, w=None, h=None):
    """归档当前本、开一本新的并切过去。返回 (ok, msg, turned)。
    title 起个名（不指定自动「第 N 本」）；pages 这本几页（默认 24）；w/h 纸面尺寸（默认 900×600）。"""
    with _lock:
        idx = load_index()
        old = _meta(idx, _current_book_id(idx))
        if old is not None and not old.get("closed_ts"):
            old["closed_ts"] = int(time.time())
            save_index(idx)
        size = {"w": w, "h": h} if (w or h) else (old.get("size") if old else None)
        m = _create_book(idx, title=title, pages_per_book=pages, size=size)
        no = len(idx["books"])
        return True, "换了一本新的：《%s》（第 %d 本，%d 页）" % (m["title"], no, _pages_per_book(m)), True


def _find_book(idx, ref):
    """按 id 精确 / 序号（1 基）/ 标题包含 找一本；找不到返回 None。"""
    ref = str(ref or "").strip()
    if not ref:
        return None
    for b in idx.get("books") or []:
        if b.get("id") == ref:
            return b
    if ref.isdigit():
        i = int(ref)
        bs = idx.get("books") or []
        if 1 <= i <= len(bs):
            return bs[i - 1]
    for b in idx.get("books") or []:
        if ref in str(b.get("title") or ""):
            return b
    return None


def goto_book(ref):
    """切当前写作本（id / 序号 1 基 / 标题包含）。返回 (ok, msg, turned)。"""
    with _lock:
        idx = load_index()
        b = _find_book(idx, ref)
        if b is None:
            return False, "没找到那一本", False
        idx["current"] = b["id"]
        save_index(idx)
        return True, "切到《%s》了（第 %d 本）" % (b.get("title") or "", (idx["books"].index(b) + 1)), False


# ---------------------------------------------------------------- 封面 / 书签

def cover(svg=None, image=None, book_id=None):
    """给本子画封面：svg（他手写的 SVG，默认这条路）或 image（一张图的地址）。
    两个都不给 → 擦掉封面。不指定 book_id 就是当前写作本。返回 (ok, msg, turned)。"""
    svg = str(svg or "").strip()
    image = str(image or "").strip()
    with _lock:
        idx = load_index()
        bid = _safe_bid(book_id) or _current_book_id(idx)
        m = _meta(idx, bid)
        if m is None:
            return False, "没找到那一本", False
        if svg:
            m["cover"] = {"kind": "svg", "data": svg}
            msg = "封面画好了（画的 SVG）"
        elif image:
            m["cover"] = {"kind": "image", "data": image}
            msg = "封面换上了（用的图）"
        else:
            m["cover"] = None
            msg = "封面擦掉了"
        save_index(idx)
        return True, msg, False


def add_bookmark(page=None, book_id=None):
    """人在页面上手动夹书签：默认夹当前写作页；同一页重复夹只留一条。
    书签只在本子里有效（跟着这本文件走）。返回 (ok, msg, turned)。"""
    with _lock:
        idx = load_index()
        bid = _safe_bid(book_id) or _current_book_id(idx)
        if _meta(idx, bid) is None:
            return False, "没找到那一本", False
        d = _read_book_file(bid)
        _ensure_page(d)
        pages = d["pages"]
        try:
            p = int(page)
        except (TypeError, ValueError):
            p = d.get("current") or 0
        p = max(0, min(len(pages) - 1, p))
        bms = d.setdefault("bookmarks", [])
        for b in bms:
            if b.get("page") == p:
                return True, "这页已经夹了书签", False
        bms.append({"id": _new_id("bm"), "page": p, "created_ts": int(time.time())})
        bms.sort(key=lambda b: b.get("page") or 0)
        _write_book_file(bid, d)
        return True, "夹上书签了（第 %d 页）" % (p + 1), False


def remove_bookmark(ref, book_id=None):
    """摘书签：ref 认书签 id、页码（1 基）或页码数字字符串。返回 (ok, msg, turned)。"""
    ref = str(ref or "").strip()
    if not ref:
        return False, "要摘哪一条书签", False
    with _lock:
        idx = load_index()
        bid = _safe_bid(book_id) or _current_book_id(idx)
        if _meta(idx, bid) is None:
            return False, "没找到那一本", False
        d = _read_book_file(bid)
        bms = d.setdefault("bookmarks", [])
        for b in list(bms):
            if str(b.get("id")) == ref or (ref.isdigit() and b.get("page") == int(ref) - 1):
                bms.remove(b)
                _write_book_file(bid, d)
                return True, "书签摘了", False
        return False, "没找到那条书签", False
