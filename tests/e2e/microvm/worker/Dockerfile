# public.ecr.aws/lambda/microvms:al2023-minimal (the previously pinned
# al2023-minimal-2024.12 snapshot tag has since been pruned by the registry;
# refresh this digest when intentionally moving to a newer snapshot)
FROM public.ecr.aws/lambda/microvms:al2023-minimal@sha256:53cdb05519dd396022a51cadd61935665a8c056c733e8a56f8ab13a3e7288108

RUN dnf install -y python3 python3-pip && dnf clean all && \
    pip3 install --no-cache-dir "boto3>=1.35,<2"

RUN useradd -r -s /sbin/nologin worker
USER worker

COPY app.py /app/app.py

WORKDIR /app
CMD ["python3", "app.py"]
