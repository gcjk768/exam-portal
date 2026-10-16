# Exam Portal: FastAPI site that drives the quiz bots' own bot.py (mounted from their stacks) + Claude Code + Graphviz.
FROM python:3.12-slim
ARG CLAUDE_CODE_VERSION=
ARG PUID=1000
ARG PGID=1000
ENV TZ=Asia/Singapore DISABLE_AUTOUPDATER=1 PYTHONUNBUFFERED=1 \
    PATH=/home/quiz/.local/bin:$PATH PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates bash tzdata graphviz \
    && rm -rf /var/lib/apt/lists/*
RUN (getent group "$PGID" >/dev/null || groupadd -g "$PGID" quiz) \
    && useradd -m -u "$PUID" -g "$PGID" -s /bin/bash quiz
USER quiz
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_CODE_VERSION} && claude --version
USER root
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY web.py ./
COPY static ./static
COPY study ./study
# the NAS creates files 770 root-owned; the app runs as quiz, so make the copies world-readable
RUN chmod -R a+rX /app
USER quiz
EXPOSE 8000
CMD ["python", "web.py"]
