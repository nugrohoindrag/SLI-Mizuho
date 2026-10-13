# Dockerfile
FROM python:3.12.8

# Jangan buat file .pyc dan flush output
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Tambah user non-root
RUN useradd -m appuser

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Ubah ownership logs agar tidak dibuat oleh root
RUN mkdir -p logs && chown -R appuser:appuser logs

RUN mkdir -p /app/staticfiles && chown -R appuser:appuser /app/staticfiles

# Gunakan user non-root
USER appuser

RUN python manage.py collectstatic --noinput

CMD ["gunicorn", "EFidusia.wsgi:application", "--bind", "0.0.0.0:8000"]
