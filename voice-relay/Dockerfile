# voice-relay: 语音中继（WebSocket 回环）
# 依赖 websockets 库，AGENT_KEY 认证
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir websockets
COPY voice_relay.py /app/voice_relay.py
EXPOSE 8085
CMD ["python", "voice_relay.py"]
