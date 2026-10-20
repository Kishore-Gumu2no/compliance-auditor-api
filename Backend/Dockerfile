FROM python:3.11-slim
# This is the base OS image, which is linux with python 3.11 installed, it is a slim version, which means it has less packages installed
WORKDIR /app
# This is used to create and set the working directory to /app, all the commands will be run from this directory
RUN apt-get update && apt-get install -y tesseract-ocr libtesseract-dev poppler-utils
# This is used to install the dependencies for the project, that cloud platform doesn't have.
COPY requirements.lock .
# This is used to copy the requirements.txt file from the local machine to the container, to install dependencies
RUN pip install --upgrade pip
# pip 24.0 ships in python:3.11-slim and mis-handles a truncated PyPI JSON
# index page by raising JSONDecodeError instead of retrying. Upgrading first.
RUN --mount=type=cache,target=/root/.cache/pip pip install --default-timeout=60 --retries 10 -r requirements.lock
# --default-timeout is 60s, not 1000s: these are 6-43 kB metadata files and
# multi-MB wheels, so a request still alive after a minute is a dead socket,
# and waiting 1000s for it only delays finding that out. A dropped connection
# cost 11 minutes under the old value; ten retries at 60s now cover the same
# flakiness in a tenth of the time.
#
# The cache mount replaces --no-cache-dir: it keeps the image just as small,
# because a BuildKit cache never becomes a layer, but a failed build no longer
# throws away everything it already downloaded.
COPY . .
# This is used to copy the entire project from the local machine to the container
CMD ["uvicorn", "api:server", "--host", "0.0.0.0", "--port", "8000"]
# api:server is the FastAPI instance (api:app is the LangGraph workflow imported from orchestrator)

