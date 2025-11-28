# 🌐 Babelmate: Universal Dual-Language Translation Bot

A high-concurrency Telegram bot for focused bi-directional and dual-display translation, powered by **Google Cloud Translation API (v3)**.

## ✨ Key Features

* **Focus:** Bi-directional translation between two specific languages (L_A ↔ L_B).
* **Dual-Display:** Other languages translate to *both* L_A and L_B simultaneously.
* **Performance:** High-concurrency, Rate Limiting, and Admin `/stats`.

## ⚙️ Setup & Deployment

1.  **Configuration:** Edit `config.json` (set Telegram `bot_token`, Google `project_id`, `google_credentials` path, and L_A/L_B details).
2.  **Google Credentials:** Place your Google Service Account JSON file in the designated path.

| Method | Command |
| :--- | :--- |
| **Local Run** | `./install.sh` |
| **Docker** | `docker build -t babelmate .` |

---

# 🌐 Babelmate：通用双语翻译机器人

基于 **Google Cloud Translation API (v3)** 的高并发 Telegram 机器人，专注于双向和双重显示翻译。

## ✨ 核心功能

* **对焦翻译：** 预设双语间（L_A ↔ L_B）的双向互译。
* **双重显示：** 任何其他语言输入，同时翻译并显示为 L_A 和 L_B 两种译文。
* **性能保障：** 高并发优化、速率限制和管理员 `/stats` 统计。

## ⚙️ 设置与部署

1.  **配置：** 编辑 `config.json`（设置 Telegram `bot_token`、Google `project_id`、`google_credentials` 路径及 L_A/L_B 详情）。
2.  **Google 凭证：** 将 Google 服务账号 JSON 文件放置在指定路径。

| 运行方式 | 命令 |
| :--- | :--- |
| **本地运行** | `./install.sh` |
| **Docker 部署** | `docker build -t babelmate .` |