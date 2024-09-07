FROM python:3.11-alpine
ENV PYTHONUNBUFFERED 1

WORKDIR /solarflow/
COPY ./requirements.txt /solarflow/requirements.txt

RUN python -m venv --system-site-packages /opt/venv

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/usr/lib/python3.11/site-packages"

RUN pip install -Ur requirements.txt && \
    rm -rf /root/.cache/ && \
    rm -rf /var/cache/apk/*

COPY solarflow /solarflow/
ENTRYPOINT ["python3", "solarflow-control.py"]