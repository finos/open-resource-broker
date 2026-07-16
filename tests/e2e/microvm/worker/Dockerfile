FROM public.ecr.aws/lambda/microvms:al2023-minimal

RUN dnf install -y python3 python3-pip && dnf clean all && pip3 install boto3

COPY app.py /app/app.py

WORKDIR /app
CMD ["python3", "app.py"]
