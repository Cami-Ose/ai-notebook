"""
selftest.py — 离线自测（不联网、不起服务、不碰 notebook/ 里的真数据）

把数据层的主要分支跑一遍：写/划/贴胶带/翻页/写满换本/封面/书签/多本切换/坏数据。
跑法：python3 selftest.py
"""

import json
import os
import shutil
import tempfile

import notebook_store as nb

fails = []
TMP = None


def ok(cond, name):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


def setup():
    """把数据目录指到一个临时目录 —— 真数据一个字节都不碰。"""
    global TMP
    TMP = tempfile.mkdtemp(prefix="notebook-selftest-")
    nb.NOTEBOOK_DIR = TMP
    nb.INDEX_FILE = os.path.join(TMP, "index.json")


def teardown():
    if TMP:
        shutil.rmtree(TMP, ignore_errors=True)


# ---------------------------------------------------------------- ① 冷启动
print("== ① 冷启动 ==")
setup()
v = nb.view()
ok(v["page_count"] == 1, "空本子起手就有一页")
ok(v["current"] == 0, "写作页是第一页")
ok(v["book"]["pages_per_book"] == 24, "默认一本 24 页")
ok(v["book"]["size"] == {"w": 900, "h": 600}, "默认纸面 900×600")
ok(len(nb.books()) == 1, "书架上一本")
ok(os.path.exists(nb.INDEX_FILE), "index.json 落盘了")

# ---------------------------------------------------------------- ② 写 / 划 / 胶带
print("== ② 写 / 划 / 胶带 ==")
ok(nb.write("第一条")[0] is True, "写一条")
ok(len(nb.view()["pages"][0]["elements"]) == 1, "纸上多了一条")
ok(nb.write("第二条", x=100, y=200, rotation=-4)[0] is True, "指定坐标写")
ok(nb.strike("第一条")[0] is True, "划掉第一条")
ok(nb.view()["pages"][0]["elements"][0].get("struck") is True, "划掉的标记在")
ok(nb.unstrike("第一条")[0] is True, "取消划掉")
ok(nb.view()["pages"][0]["elements"][0].get("struck") is False, "标记没了")
ok(nb.strike("不存在的那条")[0] is False, "划不存在的一条要报错")
ok(nb.tape(ref="第二条")[0] is True, "给第二条贴胶带")
els = nb.view()["pages"][0]["elements"]
ok(any(e["type"] == "tape" for e in els), "胶带在纸上")
ok(len(els) == 3, "胶带是追加，不挤走别人")
ok(nb.tape(x=50, y=60)[0] is True, "也能直接给坐标贴")
ok(nb.view()["pages"][0]["elements"][-1]["type"] == "tape", "两条胶带都在")

# ---------------------------------------------------------------- ③ 翻页
print("== ③ 翻页 ==")
ok(nb.goto_page(1) is not None, "翻到第 1 页")
ok(nb.view()["current"] == 0, "翻页真的生效了")
ok(nb.goto_page(999) is not None, "翻到不存在的页不炸")
ok(nb.view()["current"] == 0, "越界就停在原地")
page_no, rolled = nb.new_page()
ok(page_no == 2 and rolled is False, "开新的一页（还没换本）")
ok(len(nb.view()["pages"]) == 2, "本子有两页了")

# ---------------------------------------------------------------- ④ 写满换本
print("== ④ 写满换本 ==")
nb.new_book(title="小的", pages=2)
ok(nb.view()["book"]["pages_per_book"] == 2, "新本 2 页上限")
ok(nb.add_image("https://example.com/a.png", w=900, h=500)[0] is True, "贴第一张图")
ok(nb.add_image("https://example.com/b.png", w=900, h=500)[2] is True, "第一页放不下 → 翻页")
ok(nb.view()["current"] == 1, "翻到第 2 页了")
ok(nb.add_image("https://example.com/c.png", w=900, h=500)[2] is True, "第 2 页也放不下 → 换本")
v = nb.view()
ok(v["book"]["no"] == 3, "现在写的是第 3 本")
ok(v["book"]["pages_per_book"] == 2, "新本继承了 2 页上限")
ok(len(nb.books()) == 3, "书架上有三本")
ok(nb.books()[-1]["current"] is True, "最新那本是当前写作本")

# ---------------------------------------------------------------- ⑤ 封面
print("== ⑤ 封面 ==")
SVG = "<svg viewBox='0 0 10 10'><rect width='10' height='10'/></svg>"
ok(nb.cover(svg=SVG)[0] is True, "画一张封面")
ok(nb.view()["book"]["cover"]["kind"] == "svg", "封面是 SVG")
ok(nb.cover(image="https://example.com/cover.png")[0] is True, "换成一张图")
ok(nb.view()["book"]["cover"]["kind"] == "image", "封面换成图了")
ok(nb.cover()[0] is True, "两个都不传 = 擦掉封面")
ok(nb.view()["book"]["cover"] is None, "封面擦掉了")

# ---------------------------------------------------------------- ⑥ 书签
print("== ⑥ 书签 ==")
nb.new_book(title="夹书签用", pages=5)
for _ in range(3):
    nb.new_page()      # 多翻几页出来，才夹得出「不同页」
bid = nb.current_book_id()
ok(nb.view()["page_count"] >= 3, "这本翻出三页以上了（现在 %d 页）" % nb.view()["page_count"])
ok(nb.add_bookmark(0)[0] is True, "夹一个书签")
ok(len(nb.view()["bookmarks"]) == 1, "书签在")
ok(nb.add_bookmark(0)[0] is True, "同一页再夹一次")
ok(len(nb.view()["bookmarks"]) == 1, "同一页只留一条")
ok(nb.add_bookmark(1)[0] is True, "再夹一页")
ok([m["page"] for m in nb.view()["bookmarks"]] == [0, 1], "书签按页码排好")
ok(nb.remove_bookmark("1")[0] is True, "摘掉第 1 页那个")
ok([m["page"] for m in nb.view()["bookmarks"]] == [1], "剩的是第 2 页那个")
ok(nb.remove_bookmark("没这个 id")[0] is False, "摘不存在的书签要报错")
last = nb.view()["page_count"] - 1
ok(nb.add_bookmark(999)[0] is True, "夹一个越界的页码")
ok([m["page"] for m in nb.view()["bookmarks"]] == [1, last], "越界就夹到最后那页")
# 书签只在本子里有效：换一本看，书签是空的
ok(nb.new_book(title="另一本")[0] is True, "再开一本")
ok(nb.view()["bookmarks"] == [], "新本没有书签")
ok(len(nb.view(bid)["bookmarks"]) == 2, "按 id 看回原来那本，书签还在")

# ---------------------------------------------------------------- ⑦ 多本切换
print("== ⑦ 多本切换 ==")
ok(nb.goto_book("1")[0] is True, "按序号切回第一本")
ok(nb.view()["book"]["no"] == 1, "切对了")
ok(nb.goto_book("不存在的本")[0] is False, "切不存在的本要报错")
ok(nb.new_book(title="大本子", pages=60, w=1200, h=800)[0] is True, "开一本大的")
v = nb.view()
ok(v["book"]["title"] == "大本子", "名字对了")
ok(v["book"]["size"] == {"w": 1200, "h": 800}, "纸面尺寸跟着走")
ok(v["book"]["pages_per_book"] == 60, "页数跟着走")

# ---------------------------------------------------------------- ⑧ 坏数据 / 越界
print("== ⑧ 坏数据 / 越界 ==")
ok(nb.view("../../etc/passwd")["book"]["id"] == nb.current_book_id(), "非法 book id 被挡掉")
ok(nb.view("")["book"]["id"] == nb.current_book_id(), "空 book id 不炸")
ok(nb.write("")[0] is False, "写空字符串要报错")
ok(nb.write("   ")[0] is False, "写空白也要报错")

bad = os.path.join(nb.NOTEBOOK_DIR, "b_broken.json")
with open(bad, "w", encoding="utf-8") as f:
    f.write("这不是 json")

with open(nb.INDEX_FILE, "w", encoding="utf-8") as f:
    f.write("{ 这不是 json")
v = nb.view()
ok(len(nb.books()) >= 1, "index.json 坏了能按本子文件重建")
ok(v["book"]["id"], "还是能拿到一本")
ok(nb.view("b_broken")["page_count"] == 1, "单本文件坏了也能起（给个空本子）")

teardown()
print()
print("FAILED %d 项" % len(fails) if fails else "全过了。")
raise SystemExit(1 if fails else 0)
