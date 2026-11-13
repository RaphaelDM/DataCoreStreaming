FROM python:3.12-slim

RUN pip install --no-cache-dir \
    jupyterlab \
    pandas \
    sqlalchemy \
    psycopg2-binary \
    matplotlib \
    seaborn

WORKDIR /workspace
EXPOSE 8888

CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--ServerApp.token=", "--ServerApp.root_dir=/workspace"]
