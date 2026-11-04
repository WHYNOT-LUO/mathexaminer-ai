# Backup host for the public demo: a Hugging Face Space (Docker SDK).
# publish.py copies this file to the root of the Space, next to the app's code.
FROM python:3.12-slim

# Spaces run the container as user 1000; give it a writable home for Streamlit's files.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

EXPOSE 7860
# The Space shows the app inside an iframe on huggingface.co, where Streamlit's XSRF cookie is
# blocked and uploads would fail; the demo holds no secrets, so the protection is switched off.
CMD ["streamlit", "run", "demo/demo_app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true", \
     "--server.enableXsrfProtection=false", "--server.enableCORS=false", \
     "--browser.gatherUsageStats=false"]
