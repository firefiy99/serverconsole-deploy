# NapCat WebUI 手机端滚动修复（viewport）

## 问题

NapCat WebUI 的 `index.html` 写死了 viewport：

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta key="viewport" content="viewport-fit=cover, width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=0" name="viewport" />
```

- `width=device-width` → 强制按手机窄屏宽度渲染（切桌面 UA 无效）
- `maximum-scale=1.0, user-scalable=0` → 禁止缩放
- 结果：设置弹窗内容超高、弹窗内部滚动失效，**保存按钮被截在屏幕外，页面拉不到底**

## 修复

把两处 viewport 都改为：

```html
<meta name="viewport" content="width=1280, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes" />
```

页面按 1280 桌面宽度渲染，WebView/浏览器自动缩小适配屏幕，内容完整可滚动，保存按钮可达。

## 应用方式

```bash
# 1. 备份并覆盖容器内 index.html（修改版见 napcat-index-fixed.html）
docker cp napcat:/app/napcat/static/index.html /opt/napcat-webui-backup/index.html.orig
docker cp napcat-index-fixed.html napcat:/app/napcat/static/index.html

# 2. 持久化：把修复版存到宿主机，container-guard.sh 会在容器重建后自动重新注入
cp napcat-index-fixed.html /opt/napcat-webui-backup/index.html.fixed
# container-guard.sh 已内置 napcat viewport 检测+恢复逻辑（每 2 分钟 cron 执行）
```

注意：容器重建/升级后 index.html 会恢复原版，需要靠 `container-guard.sh` 的持久化逻辑自动重新注入（见 `scripts/container-guard.sh` 末尾）。
