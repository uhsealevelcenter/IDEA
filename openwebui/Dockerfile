# The published IDEA customization includes Open Sharing and terminal support.
# Apply IDEA's preprocessing rules while retaining the published frontend.
FROM ghcr.io/uhsealevelcenter/idea-open-webui:0.12.5-idea.0.11@sha256:28b9230d601628fe2ea879a41f751015999863467f8aa63197528f2360e4de76

COPY patch_preprocessing.py test_preprocessing.py /tmp/idea-preprocessing/
RUN python /tmp/idea-preprocessing/patch_preprocessing.py /app/backend/open_webui/utils/middleware.py \
    && python /tmp/idea-preprocessing/test_preprocessing.py \
    && rm -r /tmp/idea-preprocessing
