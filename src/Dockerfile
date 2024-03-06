FROM python:3.11-alpine as compiler
ENV PYTHONUNBUFFERED 1

WORKDIR /solarflow/

RUN python -m venv /opt/venv
# Enable venv
ENV PATH="/opt/venv/bin:$PATH"

COPY ./requirements.txt /solarflow/requirements.txt
RUN pip install -Ur requirements.txt

FROM python:3.11-alpine as runner
WORKDIR /solarflow/
COPY --from=compiler /opt/venv /opt/venv

# Enable venv
ENV PATH="/opt/venv/bin:$PATH"
COPY . /solarflow/
CMD ["python", "solarflow-control.py", ]