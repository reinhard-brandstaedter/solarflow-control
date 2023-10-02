FROM python:3.10-alpine

# Create stdconfig directory
WORKDIR /

COPY solarflow-control.py /

RUN pip install --upgrade pip
RUN apk add --no-cache gcc
RUN apk add --no-cache linux-headers


RUN apk add --no-cache --virtual .build-deps gcc libc-dev libxslt-dev && \
    apk add --no-cache libxslt && \
    pip install --no-cache-dir lxml>=3.5.0 && \
    apk del .build-deps
RUN pip install cffi paho-mqtt astral click ip2geotools

ENTRYPOINT ["python","solarflow-control.py"]