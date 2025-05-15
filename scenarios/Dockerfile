FROM python:3.13@sha256:653b0cf8fc50366277a21657209ddd54edd125499d20f3520c6b277eb8c828d3

WORKDIR /usr/src/app/

ENV POETRY_VERSION=2.1.1
ENV POETRY_HOME=/opt/poetry
RUN curl --proto "=https" --tlsv1.2 -sSf -L https://install.python-poetry.org | python -


ENV PATH="/opt/poetry/bin:$PATH"
RUN poetry config virtualenvs.in-project true

# Setup project
COPY pyproject.toml poetry.lock README.md ./
RUN poetry install

COPY examples/ examples/

ENTRYPOINT ["poetry", "run"]