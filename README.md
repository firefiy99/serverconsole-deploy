# ServerConsole 三端协同系统

一套开箱即用的 **AstrBot + QQ 机器人 + 服务器监控** 全家桶部署方案：一条命令在你的云服务器上部署完整生态，用手机 App 随时管理。

> 创作者：小星萤

## ✨ 功能

| 组件 | 说明 |
|---|---|
| **控制台面板** | 深色科技风聚合监控：服务状态、Pi 编码助手、知识库、远程协助、安全防护、外观自定义 |
| **AstrBot** | 聊天机器人核心（支持 QQ/微信/Telegram 等） |
| **NapCat** | QQ 协议端（扫码登录即用） |
| **GsCore** | 游戏数据查询（原神/崩铁等） |
| **DSH** | DeepSeek Harness 编程助手（可选） |
| **手机 App** | WebView 壳，填服务器地址+密钥即可远程管理 |

## 🚀 快速部署（方案 C：全家桶）

### 前提
- 一台云服务器（Linux x86_64/aarch64，2G 内存以上）
- 已安装 Docker（未安装会自动提示）

### 步骤

```bash
# 1. 获取项目
git clone https://github.com/firefiy99/serverconsole-deploy.git
cd serverconsole-deploy

# 2. 一键安装（首次运行生成 .env）
bash install.sh

# 3. 编辑 .env 填写配置
vim .env
#   AGENT_KEY=你的随机密钥
#   QQ_ACCOUNT=你的QQ号

# 4. 再次运行完成部署
bash install.sh
```

### 部署后访问

| 服务 | 地址 |
|---|---|
| 控制台面板 | `http://你的IP:8000` |
| AstrBot | `http://你的IP:6185` |
| NapCat | `http://你的IP:6099` |
| GsCore | `http://你的IP:8765` |
| DSH | `https://你的IP:8443`（可选） |

### 手机 App 连接

1. 下载 ServerConsole App：[点此下载最新版 APK](https://github.com/firefiy99/serverconsole-deploy/releases/latest)（或到 [Releases 页面](https://github.com/firefiy99/serverconsole-deploy/releases) 选版本）
2. 打开 App → 配置服务器地址 `http://你的IP:8000` + 密钥 `AGENT_KEY`
3. 完成，随时管理你的服务器

## 🎨 自定义（去品牌化）

面板默认名称「控制台」，logo「控」。可通过 `.env` 自定义：

```bash
PANEL_NAME=我的面板
PANEL_LOGO=星
PANEL_SUB=我的服务器
```

## 📁 目录结构

```
serverconsole-deploy/
├── docker-compose.yml   # 全家桶编排
├── install.sh           # 一键部署脚本
├── .env.example         # 环境变量模板
├── panel/               # 控制台面板源码
│   ├── app.py           # Flask 后端
│   ├── index.html       # 首页
│   └── ...
├── voice-relay/         # 语音中继
│   ├── Dockerfile
│   └── voice_relay.py
└── data/                # 运行时数据（自动生成）
```

## 🔒 安全提示

- 部署后**务必修改 AGENT_KEY**
- 云服务商安全组只放行需要暴露的端口（8000/6185/6099）
- 面板内置登录失败封禁、危险命令拦截等防护

## 📜 更新日志

- **v1.2.0** (2026-08-19)：语音通话升级标准 Realtime 接口（JSON 控制帧 + 音频帧），支持服务商选择（千问/智谱/阿里云/腾讯云/OpenAI/自定义），API Key 可后填；voice_relay 重写为标准语音网关（未配置 Key 时返回明确提示）；管理页语音设置改为服务商下拉 + 模型/音色/语言；获取模型接口地址自动归一化（支持填完整 chat/completions 端点）。
- **v1.1.0** (2026-08-18)：面板运维脚本页内置 16 个默认脚本（一键重启 AstrBot/NapCat/GsCore、容器状态总览、查看日志、磁盘检查、备份 AstrBot/面板、清理容器日志、网络连通性检查、镜像列表、一键重启全部、内存 TOP10、QQ 运行状态等），支持用户自定义；首页右上角时间改为 24 小时制；nginx 代理超时提升至 300s（支持长时备份脚本）；备份脚本自动保留最近 3 份防磁盘膨胀。
- **v1.0.0** (2026-08-18)：初始发布。全家桶 docker-compose 一键部署；面板去品牌化可自定义名称；App 支持本地上传背景图；修复 App 文件选择器。
