FROM baseimage

RUN pip install Jinja2 requests
# Create stdconfig directory
WORKDIR /solarflow

COPY solarflow /solarflow

ENTRYPOINT ["python","solarflow-control.py"]
