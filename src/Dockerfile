FROM python:3.10-alpine

# Create stdconfig directory
WORKDIR /

COPY solarflow-control.py /

# dependencies needed for ARMv6/7 builds
RUN pip install --upgrade pip && \
    apk add libxml2-dev libxslt-dev libffi libffi-dev gcc musl-dev rust cargo openssl-dev && \
    pip install paho-mqtt astral ip2geotools && \
    apk del openssl-dev libffi-dev xz-dev libxml2-dev libxslt-dev openssl-dev linux-headers musl-dev gcc rust cargo && \
    rm -rf /root/.cache/ && \
    rm -rf /var/cache/apk/*

ENTRYPOINT ["python","solarflow-control.py"]
