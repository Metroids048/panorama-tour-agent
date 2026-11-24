ARG BASE=web-tutorial-browser:1.62.1-r1
FROM ${BASE} AS deps
USER root
COPY --from=wheels / /tmp/wheels/
RUN PYTHONPATH=/tmp/wheels/pip-25.3-py3-none-any.whl python3 -m pip install --no-index --no-deps --no-compile --target /opt/space-preview-python /tmp/wheels/numpy-*.whl /tmp/wheels/pillow-*.whl /tmp/wheels/opencv_python_headless-*.whl
FROM ${BASE}
USER root
ENTRYPOINT []
CMD ["sleep", "infinity"]
COPY --from=deps /opt/space-preview-python /opt/space-preview-python
ENV PYTHONPATH=/opt/space-preview-python PYTHONDONTWRITEBYTECODE=1
RUN python3 -c 'import cv2,numpy,PIL; assert (cv2.__version__,numpy.__version__,PIL.__version__)==("5.0.0","2.0.2","11.3.0")' && test -r /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc && command -v socat
