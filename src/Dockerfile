FROM python:3.10-alpine

# Create stdconfig directory
WORKDIR /

COPY solarflow-control.py /

RUN pip install paho-mqtt astral click ip2geotools
ENTRYPOINT ["python","solarflow-control.py"]