# public.ecr.aws/lambda/microvms:al2023-minimal (the previously pinned
# al2023-minimal-2024.12 snapshot tag has since been pruned by the registry;
# refresh this digest when intentionally moving to a newer snapshot)
FROM public.ecr.aws/lambda/microvms:al2023-minimal@sha256:48598370c9d72e1b5fad669ac90c4370d88a3d9ec517d2f3c39092af646839ce

COPY requirements.txt /tmp/requirements.txt
# Python 3.11 rather than the default 3.9 so current boto3 and urllib3 2.x resolve.
RUN dnf install -y python3.11 python3.11-pip shadow-utils && dnf clean all && \
    python3.11 -m pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

RUN useradd -r -s /sbin/nologin worker
USER worker

COPY app.py /app/app.py

WORKDIR /app
CMD ["python3.11", "app.py"]
