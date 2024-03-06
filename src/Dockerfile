ARG IMAGE=rbrandstaedter/solarflow-control-baseimage:latest
FROM $IMAGE

RUN pip install Jinja2 requests
# Create stdconfig directory
WORKDIR /solarflow

COPY solarflow /solarflow
ENV PYTHONPATH=/usr/lib/python3.11/site-packages

ENTRYPOINT ["python","solarflow-control.py"]