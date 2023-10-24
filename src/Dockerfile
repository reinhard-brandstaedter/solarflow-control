FROM rbrandstaedter/solarflow-control-baseimage:latest

# Create stdconfig directory
WORKDIR /solarflow

COPY solarflow /solarflow

ENTRYPOINT ["python","solarflow-control.py"]
