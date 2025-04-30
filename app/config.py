from logging import log
import logging
import os

from app import token_storage

# --- Cấu hình cơ bản ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# --- Cấu hình Firebase ---
# Đường dẫn đến tệp Service Account Key JSON
# Đảm bảo tệp này nằm ở thư mục gốc của dự án (cùng cấp với run.py)
SERVICE_ACCOUNT_KEY_PATH = os.path.join(os.path.dirname(BASE_DIR), 'nhandientienghetapp-firebase-adminsdk-fbsvc-ad9903830b.json')

# --- Cấu hình Server ---
# Địa chỉ IP và cổng cho Flask server (HTTP)
FLASK_HOST = "0.0.0.0"  # Lắng nghe trên tất cả các interface
FLASK_PORT = 5000         # Cổng HTTP chuẩn

# --- Cấu hình UDP (Placeholder) ---
UDP_HOST = "0.0.0.0"
UDP_PORT = 5005

# --- Cấu hình Scheduler ---
NOTIFICATION_INTERVAL_SECONDS = 60 # Gửi thông báo sau mỗi 60 giây

# --- Cấu hình ML (Placeholder) ---
# MODEL_PATH = os.path.join(os.path.dirname(BASE_DIR), 'model.pkl')


