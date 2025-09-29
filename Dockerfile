# Dockerfile

# Use official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code
COPY bot.py .

# Start the bot
CMD ["python", "bot.py"]
