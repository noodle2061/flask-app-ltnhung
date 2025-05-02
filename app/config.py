# app/config.py
import os
import logging
import torch
# Thêm thư viện dotenv để tự động load file .env (tùy chọn nhưng tiện lợi)
# Cần cài đặt: pip install python-dotenv
from dotenv import load_dotenv

# --- Tải biến môi trường từ file .env ---
# Tìm file .env ở thư mục gốc của dự án (server/)
# PROJECT_ROOT được định nghĩa ở dưới, nên cần lấy đường dẫn tương đối trước
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
# Load file .env nếu tồn tại
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    logging.info(f"Đã tải biến môi trường từ: {dotenv_path}")
else:
    logging.warning(f"Không tìm thấy file .env tại: {dotenv_path}. Sử dụng biến môi trường hệ thống (nếu có).")
# ----------------------------------------


# --- Cấu hình cơ bản ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# --- Cấu hình Firebase ---
# Đọc đường dẫn từ biến môi trường, nếu không có thì dùng đường dẫn mặc định (hoặc báo lỗi)
SERVICE_ACCOUNT_KEY_PATH_ENV = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY_PATH')
if SERVICE_ACCOUNT_KEY_PATH_ENV:
    SERVICE_ACCOUNT_KEY_PATH = SERVICE_ACCOUNT_KEY_PATH_ENV
    logging.info("Sử dụng đường dẫn Firebase key từ biến môi trường.")
else:
    # Nếu không có biến môi trường, có thể đặt giá trị mặc định hoặc báo lỗi
    SERVICE_ACCOUNT_KEY_PATH = os.path.join(PROJECT_ROOT, 'nhandientienghetapp-firebase-adminsdk-fbsvc-ad9903830b.json') # Giữ lại làm fallback?
    logging.warning("Biến môi trường FIREBASE_SERVICE_ACCOUNT_KEY_PATH không được đặt. Sử dụng đường dẫn mặc định (có thể không an toàn).")
    # Hoặc raise Exception("FIREBASE_SERVICE_ACCOUNT_KEY_PATH environment variable not set.")

# --- Cấu hình Server ---
# Đọc từ biến môi trường hoặc dùng giá trị mặc định
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000)) # Chuyển sang int

# --- Cấu hình UDP Server ---
UDP_HOST = os.getenv("UDP_HOST", "0.0.0.0")
UDP_PORT = int(os.getenv("UDP_PORT", 5005)) # Chuyển sang int
UDP_BUFFER_SIZE = 4096 # Giữ nguyên hoặc thêm vào .env nếu cần

# --- Cấu hình Định dạng Âm thanh --- (Giữ nguyên)
AUDIO_SAMPLE_RATE = 16000
AUDIO_BYTES_PER_SAMPLE = 4
AUDIO_NUM_CHANNELS = 1
AUDIO_NUMPY_DTYPE = '<i4'

# --- Cấu hình Xử lý và Model ML --- (Giữ nguyên)
MODEL_FILENAME = "Scream_detection_Resnet34.pt"
MODEL_PATH = os.path.join(PROJECT_ROOT,'model', MODEL_FILENAME)
AUDIO_CHUNK_DURATION_S = 1.0
AUDIO_CHUNK_SAMPLES = int(AUDIO_SAMPLE_RATE * AUDIO_CHUNK_DURATION_S)
MODEL_TARGET_LENGTH_SAMPLES = 441000
MODEL_N_MELS = 64
MODEL_N_FFT = 1024
MODEL_IMG_SIZE = (64, 862)
MODEL_CLASS_MAP = {0: 'Không hét', 1: 'Hét'}
ML_DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

# --- Cấu hình Cảnh báo Tiếng Hét --- (Giữ nguyên)
SCREAM_ALERT_COOLDOWN_S = 60
SCREAM_MIN_CONSECUTIVE_CHUNKS = 2
SCREAM_FREQUENCY_COUNT = 3
SCREAM_FREQUENCY_WINDOW_S = 10
STANDARD_ALERT_TITLE = "Cảnh báo Tiếng Hét!"
STANDARD_ALERT_BODY_TEMPLATE = "Phát hiện tiếng hét kéo dài từ thiết bị tại IP: {}"
HIGH_FREQUENCY_ALERT_TITLE = "Cảnh báo Tần Suất Hét Cao!"
HIGH_FREQUENCY_ALERT_BODY_TEMPLATE = "Phát hiện {} lần hét trong {} giây từ thiết bị tại IP: {}"

# --- Cấu hình Lưu Âm thanh --- (Giữ nguyên)
AUDIO_SAVE_DURATION_S = 10

# --- Cấu hình AWS S3 ---
# Đọc từ biến môi trường
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
AWS_S3_REGION = os.getenv("AWS_S3_REGION")

AWS_S3_AUDIO_FOLDER = "audio/" # Giữ nguyên hoặc thêm vào .env nếu cần
AWS_S3_URL_EXPIRATION_S = 3600 # Giữ nguyên hoặc thêm vào .env nếu cần

# Kiểm tra xem tất cả các biến cấu hình S3 cần thiết có giá trị hay không
S3_CONFIGURED = all([
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_S3_BUCKET_NAME,
    AWS_S3_REGION
])
# ------------------------------------------

# --- Cấu hình Scheduler --- (Giữ nguyên)
SCHEDULE_ENABLED = False
NOTIFICATION_INTERVAL_SECONDS = 60

# --- Cấu hình Logging ---
# Đọc từ biến môi trường hoặc dùng giá trị mặc định
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO) # Chuyển chuỗi thành level của logging
LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(threadName)s] - %(module)s:%(lineno)d - %(message)s'

# --- Khởi tạo Logging ---
# Xóa các handler cũ để tránh log bị lặp nếu file config được load lại
root_logger = logging.getLogger()
if root_logger.hasHandlers():
    root_logger.handlers.clear()
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)

# --- Log thông tin cấu hình ---
logging.info(f"--- Cấu hình ứng dụng đã được tải (Log Level: {LOG_LEVEL_STR}) ---")
logging.info(f"ML Device: {ML_DEVICE}")
logging.info(f"Flask Server: {FLASK_HOST}:{FLASK_PORT}")
logging.info(f"UDP Listener: {UDP_HOST}:{UDP_PORT}")
logging.info(f"Scream Alert: Min Consecutive Chunks = {SCREAM_MIN_CONSECUTIVE_CHUNKS}, Frequency = {SCREAM_FREQUENCY_COUNT} times in {SCREAM_FREQUENCY_WINDOW_S}s, Cooldown = {SCREAM_ALERT_COOLDOWN_S}s")
if S3_CONFIGURED:
    logging.info(f"AWS S3 Saving: Enabled, Bucket={AWS_S3_BUCKET_NAME}, Region={AWS_S3_REGION}, Folder={AWS_S3_AUDIO_FOLDER}, URL Expires={AWS_S3_URL_EXPIRATION_S}s")
else:
    logging.warning("AWS S3 Saving: Disabled (Một hoặc nhiều biến môi trường AWS bị thiếu: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME, AWS_S3_REGION)")

if not SERVICE_ACCOUNT_KEY_PATH_ENV:
     logging.warning("Firebase Key Path đang sử dụng giá trị mặc định trong code.")

