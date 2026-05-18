<div align="center">

# Twilight 暮光

## New Generation Emby/Jellyfin Manager

## 新一代 Emby/Jellyfin 用户管理器

# !!须知!!

## **!!该项目绝大部分由 Claude Code Opus 4.6/4.7 与 ChatGPT Codex 5.3 完成，仅有少量人工修改和润色。主贡献者对该项目安全性等不做任何保证，使用前请务必仔细审查代码。如出现问题，本项目不会承担任何责任。请了解以上信息后再决定是否使用。!!**

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/) [![Flask](https://img.shields.io/badge/Flask-3.x-green?logo=flask&logoColor=white)](https://flask.palletsprojects.com/) [![Next.js](https://img.shields.io/badge/Next.js-16.0+-black?logo=next.js&logoColor=white)](https://nextjs.org/) [![SQLite](https://img.shields.io/badge/SQLite-3-blue?logo=sqlite&logoColor=white)](https://www.sqlite.org/) [![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

</div>

---

- 主贡献者默认你具备一定的 Linux 与 Python 基础。如果完全不熟悉，建议先了解相关基础
- **关于 Docker** 作者本人不喜欢 Docker，因此 **不提供** Docker 部署方案。如果你需要 Docker，请自行实现；请勿将"没有 Docker 方案"作为 Issue 提交，否则会被直接关闭。

## ✨ 功能特性

| 模块 | 说明 |
| ---- | ---- |
| **Emby/Jellyfin 管理** | 用户注册/续期/禁用、账号绑定 |
| **求片功能** | TMDB + Bangumi 多源搜索、库存自动检查（含季度）、请求-审核流程 |
| **Bangumi 同步** | 支持账号绑定与条目同步能力，便于统一追番状态 |
| **安全** | 设备数/播放数限制、IP 黑名单、登录日志、API Key |
| **Web 管理界面** | 基于 Next.js 16 的响应式 UI，可视化配置编辑器 |
| **扩展集成** | RESTful API、API Key 外部接口、可选 Telegram Bot |

---

## 🚀 安装部署

> 详细步骤请参考 **[安装部署指南](docs/INSTALL.md)**

### 环境要求

- **Python** 3.10+（推荐 3.11+）
- **Node.js** 18+（用于前端，推荐 20+）
- **Emby/Jellyfin** 已部署的服务器

---

## 📚 文档

| 文档 | 说明 |
| ---- | ---- |
| [文档导航](docs/README.md) | 统一入口，按角色快速定位 |
| [安装部署指南](docs/INSTALL.md) | 安装、配置、部署详细步骤 |
| [后端 API 文档](docs/BACKEND_API.md) | REST API 接口说明 |
| [API Key 文档](docs/API_KEY_API.md) | 外部系统接入指南 |
| [前端开发文档](docs/FRONTEND.md) | 前端技术栈与开发指南 |
| [开发指南](docs/DEVELOPMENT.md) | 编码规范、调试、贡献流程 |
| [安全加固指南](docs/SECURITY.md) | 生产安全基线与检查清单 |

运行时访问 `/api/v1/docs` 查看 Swagger UI 交互式文档。

---

## 🙏 鸣谢

- [Emby](https://emby.media/)
- [Jellyfin](https://jellyfin.org/)
- [TheMovieDataBase](https://www.themoviedb.org/)
- [Bangumi 番组计划](https://bgm.tv/)
- [Next.js](https://nextjs.org/)
- [Sakura_embyboss](https://github.com/berry8838/Sakura_embyboss)
- [Bangumi-syncer](https://github.com/SanaeMio/Bangumi-syncer)

## 📄 许可证 License

本项目基于 [MIT License](LICENSE) 开源

```License
MIT License

Copyright (c) 2025 Prejudice Studio

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

<div align="center">

**如果这个项目对你有帮助，请给一个 ⭐ Star！**

**Made with ❤️ by [Prejudice Studio](https://github.com/Prejudice-Studio/)**

</div>
