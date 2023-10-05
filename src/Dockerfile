FROM python:3.10-alpine

# Create stdconfig directory
WORKDIR /

COPY solarflow-control.py /

RUN pip install --upgrade pip
RUN pip install paho-mqtt
RUN pip install astral
RUN pip install ip2geotools

ENTRYPOINT ["python","solarflow-control.py"]