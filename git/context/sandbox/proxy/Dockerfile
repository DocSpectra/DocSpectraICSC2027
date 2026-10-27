# Sprint 11 egress allowlist proxy. Base is the locally cached python:3.12-slim,
# pinned by digest; no package install. The allowlist is baked in so the image ID
# pins it.
FROM python@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4
COPY allowlist_proxy.py /opt/proxy/allowlist_proxy.py
COPY allowlist.txt /etc/proxy/allowlist.txt
USER 65534:65534
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 3128
ENTRYPOINT ["python", "-I", "/opt/proxy/allowlist_proxy.py"]
