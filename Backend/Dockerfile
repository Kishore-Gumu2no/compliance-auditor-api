FROM python:3.11-slim
# This is the base OS image, which is linux with python 3.11 installed, it is a slim version, which means it has less packages installed
WORKDIR /app
# This is used to create and set the working directory to /app, all the commands will be run from this directory
RUN apt-get update && apt-get install -y tesseract-ocr libtesseract-dev poppler-utils
# This is used to install the dependencies for the project, that cloud platform doesn't have.
COPY requirements.txt .
# This is used to copy the requirements.txt file from the local machine to the container, to install dependencies
RUN pip install --default-timeout=1000 --retries 5 --no-cache-dir -r requirements.txt
# This is used to install the dependencies from the requirements.txt file, --no-cache-dir is used to not cache the dependencies, so that the image size is small
COPY . .
# This is used to copy the entire project from the local machine to the container
CMD ["uvicorn", "api:server", "--host", "0.0.0.0", "--port", "8000"]
# api:server is the FastAPI instance (api:app is the LangGraph workflow imported from orchestrator)

