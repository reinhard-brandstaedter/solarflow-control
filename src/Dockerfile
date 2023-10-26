FROM rbrandstaedter/solarflow-control-baseimage:latest

RUN pip install Jinja2
# Create stdconfig directory
WORKDIR /solarflow

COPY solarflow /solarflow

ENTRYPOINT ["python","solarflow-control.py"]
