#!/bin/bash
# 容器守护：检测容器不在 running 或健康检查非 healthy 则重启
# 每 2 分钟由 cron 调用（建议带 flock 防重入）
# 用法：bash scripts/container-guard.sh
# crontab: */2 * * * * flock -xn /tmp/container-guard.lock -c 'bash /opt/container-guard.sh'
LOG=/opt/guard_status.txt
CONTAINERS="agent-monitor voice-relay nginx-proxy astrbot napcat gsuid_core dsh"

for c in $CONTAINERS; do
  if ! docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null | grep -q true; then
    echo "$(date '+%F %T') [$c] 未运行，重启" >> "$LOG"
    docker start "$c" >> "$LOG" 2>&1
    continue
  fi
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null)
  if [ "$health" = "unhealthy" ]; then
    echo "$(date '+%F %T') [$c] 不健康，重启" >> "$LOG"
    docker restart "$c" >> "$LOG" 2>&1
  fi
done
