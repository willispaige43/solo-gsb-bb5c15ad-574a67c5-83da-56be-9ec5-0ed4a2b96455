# 邮件线程重建台（Email Thread Bench）

用 **Python FastAPI + SQLite + 原生 HTML/CSS/JS** 构建的邮件会话线程重建工具。
导入包含 `Message-ID`、`Subject`、`In-Reply-To`、`References`、`Date` 的
RFC822 邮件元数据后，自动按引用关系重建会话树，支持缺失父节点占位、
循环引用断开标记、稳定排序与孤立邮件筛选。无需 Node.js、无需前端构建。

## 功能特性

- **导入 RFC822 元数据**：支持单封邮件、mbox 拼接（`From ` 分隔行）以及多封邮件直接拼接；
  可上传文件或直接粘贴原始邮件头，自动解码 `=?UTF-8?B?...?=` 等编码头。
- **线程树重建**
  - 优先使用 `In-Reply-To`，否则取 `References` 链的最后一个引用；
  - **缺失父节点**：被引用但未导入的邮件自动创建为「占位根」（紫色标记），
    引用链中多个缺失节点按链顺序串联；
  - **循环引用**：沿父指针检测环（含自引用），断开闭环处的边并以
    红色「循环断开」标记，详情中展示被断开的父 Message-ID；
  - **主题归并**：没有任何引用头的散落邮件，若归一化主题相同
    （忽略 `Re:/Fwd:` 等前缀、大小写与空白差异），归并到时间最早的一封。
- **稳定排序**：每个层级均按 `(时间, Message-ID)` 排序；线程根按子树最早时间排序；
  时间缺失的邮件稳定排在最后。
- **页面交互**：原生 HTML/CSS/JS，点击展开/折叠线程，点击邮件查看
  `Message-ID / From / In-Reply-To / References / 断环信息` 详情；
  顶部可筛选 **全部 / 仅孤立邮件 / 含循环引用 / 含缺失占位**；
  含问题节点的线程默认自动展开，浅色/深色自适应。
- **SQLite 持久化**：邮件与重建后的线程图均落库，重启容器数据不丢
  （数据保存在 Docker volume 中）。

## 目录结构

```
.
├── app/
│   ├── main.py        # FastAPI 入口与 JSON API
│   ├── parser.py      # RFC822 / mbox 解析（标准库 email 包）
│   ├── threading.py   # 线程重建：选父、断环、主题归并、稳定排序
│   └── database.py    # SQLite 持久化
├── static/
│   ├── index.html     # 页面
│   ├── style.css      # 样式（浅色/深色自适应）
│   └── app.js         # 前端交互（原生 JS）
├── examples/
│   └── sample.mbox    # 示例数据（含正常线程/占位/循环/孤立）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 一键启动（Docker Compose）

```bash
docker compose up -d --build
```

启动后访问：

- **页面地址：<http://localhost:8000/>**
- API 文档（Swagger）：<http://localhost:8000/docs>

停止：

```bash
docker compose down          # 保留数据
docker compose down -v       # 同时删除 SQLite 数据卷
```

> 端口可在 `docker-compose.yml` 的 `ports` 中修改，例如 `"8080:8000"`。

## 使用方式

1. 打开 <http://localhost:8000/>，点击右上角 **「导入邮件」**；
2. 上传 `examples/sample.mbox`（或任意 RFC822/mbox 文件），也可直接粘贴邮件头文本；
3. 页面自动重建线程：
   - 正常回复以缩进树展示；
   - 紫色 **缺失占位** 为引用了但未导入的父邮件；
   - 红色 **循环断开** 表示检测到引用环并已断开；
   - 绿色 **孤立** 表示无引用、无回复、无同主题归并的单独邮件；
4. 用顶部单选框切换筛选；点击邮件行可展开/折叠，点击邮件主体查看引用详情；
5. **「清空」** 会删除全部邮件（不可恢复）。

可识别的最小邮件格式（必须包含 `Message-ID`）：

```
Message-ID: <a@example.com>
Subject: Hello
In-Reply-To: <parent@example.com>
References: <parent@example.com>
Date: Mon, 22 Sep 2026 10:00:00 +0800
From: Alice <alice@example.com>
```

## HTTP API

| 方法   | 路径                 | 说明                                                   |
| ------ | -------------------- | ------------------------------------------------------ |
| GET    | `/`                  | 线程台页面                                             |
| POST   | `/api/import`        | 导入邮件（multipart：`file` 文件和/或 `raw` 文本）     |
| GET    | `/api/threads`       | 查询线程森林，`?filter=all\|orphans\|cycles\|placeholders` |
| DELETE | `/api/messages`      | 清空全部邮件并重置线程                                 |

`/api/threads` 返回示例：

```json
{
  "threads": [
    {
      "message_id": "<a@example.com>",
      "subject": "Project Plan",
      "from": "Alice <alice@example.com>",
      "date_ts": 1790042400.0,
      "placeholder": false,
      "is_cycle": false,
      "broken_edge": null,
      "depth": 0,
      "children": [ ... ]
    }
  ],
  "stats": {"total": 5, "thread_roots": 3, "orphans": 1,
            "placeholders": 1, "cycles": 0},
  "orphan_ids": ["<d@example.com>"],
  "missing_refs": ["<ghost@example.com>"]
}
```

## 线程重建规则

1. **选父**：`In-Reply-To` 指向的邮件为父；缺失时用 `References` 链最后一项；
   `In-Reply-To` 未出现在 `References` 中时追加到链尾。
2. **占位**：链中不存在的 Message-ID 创建占位节点；缺失节点之间按引用顺序连边，
   保证链拓扑可追溯。
3. **断环**：每个节点只有一条父边，使用三色路径遍历定位回边；
   断开「闭环节点 → 父」这条边，将其置为根，并记录 `broken_edge`。
4. **主题归并**：`References` 与 `In-Reply-To` 均为空、且未涉及断环的真实邮件，
   按归一化主题分组，其余成员挂到时间最早的邮件之下。
5. **排序**：同级节点按 `(date_ts, message_id)` 升序；无线索日期的排末尾；
   线程根按子树内最早时间排序。
6. **孤立定义**：真实邮件中，最终位于根位置且没有任何子节点的邮件。

## 本地开发（不用 Docker）

需要 Python 3.11+：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

SQLite 文件默认位于 `./data/emails.db`，可用环境变量覆盖：

```bash
EMAIL_DB_PATH=/tmp/emails.db uvicorn app.main:app --port 8000
```

核心逻辑冒烟测试（仅依赖标准库，35 个断言，覆盖解析 / 占位 / 断环 /
排序 / 主题归并 / 孤立判定）：

```bash
python3 test_core.py
```
