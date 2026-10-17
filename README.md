# 手账本

一本给模型用的手写本。模型通过工具调用往纸上写字、贴图、划掉、翻页，页面用于查看结果。

后端为单文件 Python，只用标准库；前端为两个静态文件，无构建步骤，无外部资源。

## 功能

- **写上去就固定** —— 元素带坐标和角度，按写入次序烙在纸上，不是可拖动的卡片
- **像纸一样** —— 不指定位置时自动排到下一行，字迹带轻微歪斜，写满自动翻页
- **划掉** —— 划除后保留字迹，可取消
- **胶带** —— 覆盖在指定条目上，长度按该条目自动计算
- **多本** —— 一本写满（默认 24 页）自动归档并开启新本，每本有各自的纸面尺寸
- **封面** —— 模型绘制 SVG，或指定一张图片；第 0 页显示完整封面
- **书签** —— 在页面上手动添加，仅对当前本有效
- **零依赖** —— 后端只用 Python 标准库，前端不加载 CDN、webfont 或框架
- **自己写（可选）** —— 配置模型接口后，可在页面上触发模型写入一条；未配置时由 agent 通过接口写入

## 运行

需要 Python 3.9 或以上。

```bash
python3 server.py
```

访问 http://127.0.0.1:8770。

`notebook/` 内附一本示例（两页、一个封面、一条书签），无需任何配置即可查看。

## 配置（可选）

未配置时仍可运行，本子由 agent 通过接口写入。如需让模型自主写入：

```bash
cp config.example.json config.json
```

打开 `config.json`，填写 `llm` 一节。`base_url` 填到 `/v1` 这一层，程序会自动拼接 `/chat/completions`。

| 字段 | 说明 |
|---|---|
| `host` / `port` | 监听地址，默认 `127.0.0.1:8770`。局域网内其他设备访问时，`host` 改为 `0.0.0.0` |
| `llm.base_url` | 模型接口地址，OpenAI 兼容 |
| `llm.api_key` | 接口密钥 |
| `llm.model` | 模型名 |

环境变量优先级高于 `config.json`：`NOTEBOOK_HOST` `NOTEBOOK_PORT` `LLM_BASE_URL` `LLM_API_KEY` `LLM_MODEL`。

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 页面 |
| GET | `/notebook.js` | 页面脚本 |
| GET | `/api/notebook` | 一本的全量数据（`?book=<id>` 指定哪本，缺省为当前写作本） |
| GET | `/api/notebook/books` | 书目（每本页数、书签数、有无封面） |
| GET | `/api/config` | 模型接口是否已配置、本数 |
| POST | `/api/notebook` | 写操作，`action` 见下表 |
| POST | `/api/notebook/bookmark` | 书签。body `{action:"add"\|"remove", page?, ref?, book?}` |
| POST | `/api/notebook/knock` | 触发一次模型调用，由模型决定是否写入 |

`POST /api/notebook` 的 `action`：

| action | 参数 | 用途 |
|---|---|---|
| `write` | `text` 必填；`x` `y` `rotation` `font_size` `color` 可选 | 向当前页写入一条 |
| `strike` / `unstrike` | `ref`（该条的文字或 id） | 划掉 / 取消划掉 |
| `add_image` | `src` 必填；`x` `y` `rotation` `w` `h` | 贴一张图 |
| `add_svg` | `svg` 必填；同 `add_image` | 贴一段手绘 SVG |
| `tape` | `ref` 或 `x`/`y`；`color` `opacity` `rotation` | 贴一条胶带 |
| `new_page` | —— | 换新页（已是最后一页则换新本） |
| `goto_page` | `n`（从 1 起） | 翻到指定页 |
| `new_book` | `title` `pages` `w` `h` | 归档当前本并开启新本 |
| `goto_book` | `ref`（序号从 1 起 / id / 标题） | 切换当前写作本 |
| `list_books` | —— | 书目及各自进度 |
| `cover` | `svg` 或 `image` | 设置 / 更换封面（两者均不传则清除） |

返回该本的全量视图，以及 `ok` / `msg` 与两个状态标记：`turned_page`（是否翻页）、`new_book`（是否换本）。

## 接给模型

`prompts.py` 提供现成的工具定义（`NOTEBOOK_TOOL`，OpenAI tools 格式）与系统提示
（`KNOCK_SYSTEM`）。加入工具清单即可使用，工具名为 `notebook`。

本仓库不包含人物设定：写入者与阅读对象由使用方决定。可修改 `KNOCK_SYSTEM` 的措辞，
或在其前接入自定义人设。

## 目录

```
server.py             HTTP 服务（标准库）、写操作、模型调用
prompts.py            工具定义 / 自主写入的系统提示 / 上下文注入接口
notebook_store.py     本子数据层（多本、原子写、线程安全）
notebook/             本子数据（运行时写入）
config.example.json   配置模板
static/index.html     页面（样式内联）
static/notebook.js    翻页、书架、书签、胶带、封面页
selftest.py           离线自测：python3 selftest.py
check-clean.mjs       发布前自查：通用脱敏模式与结构约束
README.md             本文件
LICENSE               MIT 许可证
NOTICE                第三方与服务说明
```

## 注意事项

- **`notebook/` 为运行时数据。** 模型的写入内容保存在此目录，仓库内的那份仅为示例。
- **配置在启动时读取。** 修改 `config.json` 后需重启 `server.py`。
- **写入是原子的。** 先写 `.tmp` 再 `os.replace`，断电或并发请求不会留下不完整文件。
- **换本自动进行。** 写到 `pages_per_book`（默认 24）时归档当前本并开启新本，继承页数上限与纸面尺寸。
- **坐标为纸面像素。** 默认纸面 900×600，前端按 `size` 缩放显示，每本可单独指定尺寸。
- **胶带不占位。** 胶带覆盖在内容上，不影响后续自动排版。
- **第 0 页为封面。** 翻页时排在正文之前，该页的「夹书签」不可用。
- **默认仅监听本机。** `host` 为 `127.0.0.1` 时仅本机可访问。服务端本身不提供鉴权，
  对外提供服务时需在前置代理上实现。
- **`knock` 每次触发一次模型调用。** 不需要此功能时不配置 `llm`，对应按钮会自动隐藏。

## 许可

MIT，见 [LICENSE](LICENSE)。第三方与服务见 [NOTICE](NOTICE)。
