FROM public.ecr.aws/lambda/microvms:al2023-minimal-2024.12

RUN dnf install -y python3 python3-pip && dnf clean all && \
    pip3 install --no-cache-dir "boto3>=1.35,<2"

RUN useradd -r -s /sbin/nologin worker
USER worker

COPY app.py /app/app.py

WORKDIR /app
CMD ["python3", "app.py"]
