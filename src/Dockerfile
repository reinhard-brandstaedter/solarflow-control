FROM python:3.10-alpine

# Create stdconfig directory
WORKDIR /

COPY solarflow-control.py /

RUN pip install requests paho-mqtt
ENTRYPOINT ["python","solarflow-control.py"]