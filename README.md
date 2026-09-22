# 📧 邮件线程重建台

用 **Python FastAPI + SQLite + 原生 HTML/CSS** 构建的邮件会话（Thread）重建工具，支持 **Docker Compose 一键启动**。

导入包含 `Message-ID`、`Subject`、`In-Reply-To`、`References` 与 `Date` 的 RFC822 邮件（`.eml`），按引用关系重建会话树并在浏览器中展开浏览。

## 功能

- **会话树重建**：依据 `In-Reply-To` / `References` 引用关系还原父子结构（References 末尾优先，其次 In-Reply-To）。
- **缺失父节点占位**：被引用但未导入的邮件自动生成虚线灰色「占位根」节点。
- **循环引用检测**：挂接前沿祖先链检测成环，成环则断开该边，将邮件标记为 ⚠「循环已断开」并升级为根节点。
- **稳定排序**：整棵树递归按 `(Date, Message-ID)` 排序；占位节点继承最早子节点的排序键。
- **展开 / 折叠**：基于原生 `<details>`，每个线程可单独展开折叠。
- **孤立邮件筛选**：「孤立邮件」标签页列出既不引用他人、也未被他人引用的单封邮件。
- **RFC822 导入**：页面多选上传 `.eml`（按 Message-ID 幂等 upsert），或一键载入内置示例数据。
- **JSON 接口**：`GET /api/threads` 返回线程森林、统计信息与孤立邮件列表。

## 启动方式

### Docker Compose（推荐）

```bash
docker compose up --build
```

启动后访问：

> **http://localhost:8000**

SQLite 数据库持久化在宿主机 `./data/mail.db`（compose 已挂载卷）。停止：`Ctrl+C`；后台运行：`docker compose up -d --build`。

### 本地直接运行（开发）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

同样访问 http://localhost:8000 。

## 使用说明

1. 打开页面后点击 **「载入示例数据」**，可立即看到 8 封示例邮件：
   - 一个 3 层正常线程（`kickoff-001 → reply-002 → reply-003`）；
   - 一个缺失父节点的线程（`orphan-reply-004` 挂在占位根 `<missing-parent-999>` 下）；
   - 一对循环引用邮件（`loop-a` 与 `loop-b` 互相 In-Reply-To，自动断开并标记）；
   - 两封孤立邮件（`solo-007`、`solo-008`，可在「孤立邮件」标签页筛选查看）。
2. 也可通过表单上传自己的 `.eml` 文件（支持多选）。
3. 点击线程标题即可展开 / 折叠；统计栏显示邮件总数、线程数、占位节点数、断开的循环数与孤立邮件数。

## 项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由（页面 / 导入 / JSON API）
│   ├── threading.py     # 线程树重建、占位、断环、稳定排序、孤立判定
│   ├── parser.py        # RFC822 元数据解析（标准库 email）
│   ├── database.py      # SQLite 存取与幂等 upsert
│   ├── templates/index.html
│   └── static/style.css
├── sample_emails/       # 内置示例 .eml（覆盖全部异常场景）
├── data/                # SQLite 数据库（运行后生成，已持久化挂载）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
