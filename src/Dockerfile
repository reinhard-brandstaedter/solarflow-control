ARG IMAGE=rbrandstaedter/solarflow-control-baseimage:latest
FROM $IMAGE

ENV VIRTUAL_ENV=/solarflow
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /solarflow

COPY solarflow /solarflow

ENTRYPOINT ["python","solarflow-control.py"]