FROM python:3.10-alpine

# Create stdconfig directory
WORKDIR /

COPY solarflow-control.py /

RUN pip install --upgrade pip
RUN apk add libxml2-dev libxslt-dev libffi libffi-dev gcc musl-dev rust cargo openssl && \
    pip install paho-mqtt astral ip2geotools && \
    apk del libxml2-dev libxslt-dev libffi-dev musl-dev gcc rust && \
    rm -rf /var/cache/apk/*


ENTRYPOINT ["python","solarflow-control.py"]