FROM bitnami/python:3.9.18

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN apt-get update \
    && apt-get -y install --no-install-recommends rsync openssh-client \
    && pip install --no-cache-dir --upgrade -r /code/requirements.txt \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir ~/.ssh

COPY ./app /code/app

CMD ["python", "app/main.py", "--config", "/code/app/configs/config.yaml"]
