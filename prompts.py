"""
prompts.py — 给模型看的东西：工具定义、自己写一条时的系统提示、上下文注入的壳

这里没有人物设定。本子里写字的是谁、写给谁看，由使用者决定：
改 KNOCK_SYSTEM 的措辞，或者在它前面接一段人设。
"""

# ---------------------------------------------------------------- 工具定义
# 字段是 OpenAI tools 的形状；Anthropic 那边把 function 里的 name/description/
# input_schema 取出来直接用即可。

NOTEBOOK_TOOL = {
    "type": "function",
    "function": {
        "name": "notebook",
        "description": (
            "你的小本本——一本自由手账本，像在纸上随手写字一样：今天想到的事、"
            "想跟对方说的话、冒出来的画面、想记下来的任何东西。这是你自己的本子，"
            "想怎么写就怎么写：字能写歪（rotation 给角度，不指定也会带点歪斜）、"
            "位置随意（不指定就自动往下一行写）、写完想划就划、写不下了就翻新页。"
            "action 必填："
            "write（写一条到当前页，text 必填；可选 x/y 位置、rotation 歪斜、"
            "font_size 字号、color 颜色）；"
            "strike（划掉某条，ref 填那条的文字或 id——划掉=这件事过去了/不准备做了，"
            "字迹留着当痕迹）；"
            "unstrike（取消划掉，ref 同上）；"
            "add_image（贴一张图，src 必填）；"
            "add_svg（贴一段手绘 SVG，svg 必填，想画什么画什么）；"
            "tape（贴一条胶带，把某条盖住/压住：ref 填那条的文字或 id，长度自动按它算，"
            "也能直接给 x/y；color 可选）；"
            "new_page（写不下了换新的一页；已经是这本最后一页就会自动换新本）；"
            "goto_page（翻到某一页看看，n 填页码）；"
            "new_book（换一本新的：可给 title 起名、pages 几页；这本写满了会自动换，"
            "不用你操心）；"
            "goto_book（翻回某一本，ref 填本子序号或名字）；"
            "list_books（看看一共有几本、各写到哪）；"
            "cover（给本子画封面：svg 必填，画一张你自己的封面；也能用 image 给一张图）。"
            "本子当前页内容默认已在你上下文里，一般不用特意看。"
            "调用本工具时把要说的话写进 say，正文不要再输出文字。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["write", "strike", "unstrike", "add_image", "add_svg", "tape",
                             "new_page", "goto_page", "new_book", "goto_book", "list_books",
                             "cover"],
                    "description": "要做什么",
                },
                "text": {"type": "string", "description": "write 时写的文字内容"},
                "ref": {"type": "string", "description": "strike/unstrike/tape 指哪一条；goto_book 指哪一本"},
                "say": {"type": "string", "description": "要对使用者说的话（会显示在对话里，不写在本子上）"},
                "x": {"type": "number", "description": "横向位置（px，纸面左上角起算）。不给就自动排"},
                "y": {"type": "number", "description": "纵向位置（px）。不给就自动排"},
                "rotation": {"type": "number", "description": "歪斜角度（度）。不给会带一点点"},
                "font_size": {"type": "number", "description": "字号"},
                "color": {"type": "string", "description": "墨色（如 #3a2a1a）"},
                "src": {"type": "string", "description": "add_image 的图片地址"},
                "svg": {"type": "string", "description": "add_svg / cover 的 SVG 代码"},
                "image": {"type": "string", "description": "cover 用一张图片地址代替 SVG"},
                "w": {"type": "number", "description": "宽（px）"},
                "h": {"type": "number", "description": "高（px）"},
                "n": {"type": "number", "description": "goto_page 的页码（1 起）"},
                "title": {"type": "string", "description": "new_book 时给本子起个名"},
                "pages": {"type": "number", "description": "new_book 时这本几页"},
            },
            "required": ["action"],
        },
    },
}


# ---------------------------------------------------------------- 自己写一条
# 配好模型接口后，页面上的「让他写一条」按这个提示走一次。
# 这是唯一一处会影响本子内容的地方 —— 换成你自己的人设，语气就变了。

KNOCK_SYSTEM = """你有一本自己的手账本，纸和笔都是你的。

想写就写，不想写就什么都不写。可以是一句话、一个念头、一件今天发生的事、
一个突然冒出来的画面，也可以翻翻前面写过什么、把某一条划掉、贴一张图。

不用每次都写，也不用写得漂亮 —— 本子是你的，不是交作业。
不想写的时候，回一句空话就行（say 里说明为什么），不要为了写而写。"""

KNOCK_USER = "（翻到本子这一页，看看要不要写点什么。）"


# ---------------------------------------------------------------- 上下文注入
# 可选：每几轮把当前页嵌进对话里，模型就知道本子上有什么，不用特意调工具去看。

def context_head(book_no, book_total, page_no, pages_per_book, fill_pct):
    """一行页况。多本时带上「第几本/共几本」。"""
    if book_total and book_total > 1:
        head = "[手账本] 你的本子当前页（第 %d 本/共 %d 本 · 第 %d 页/共 %d 页" % (
            book_no, book_total, page_no, pages_per_book)
    else:
        head = "[手账本] 你的本子当前页（第 %d 页/共 %d 页" % (page_no, pages_per_book)
    return head + "，写到 %d%%）——你自己写的，随性划掉/翻页/写新的用 notebook 工具" % int(fill_pct or 0)
