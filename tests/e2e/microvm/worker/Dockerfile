# public.ecr.aws/lambda/microvms:al2023-minimal (the previously pinned
# al2023-minimal-2024.12 snapshot tag has since been pruned by the registry;
# refresh this digest when intentionally moving to a newer snapshot)
FROM public.ecr.aws/lambda/microvms:al2023-minimal@sha256:48598370c9d72e1b5fad669ac90c4370d88a3d9ec517d2f3c39092af646839ce

RUN dnf install -y python3 python3-pip && dnf clean all && \
    pip3 install --no-cache-dir "boto3>=1.35,<2"

RUN useradd -r -s /sbin/nologin worker
USER worker

COPY app.py /app/app.py

WORKDIR /app
CMD ["python3", "app.py"]
