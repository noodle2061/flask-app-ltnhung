import os
import logging

import torch # Giữ lại import logging

# --- Cấu hình cơ bản ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR) # Thư mục gốc của dự án

# --- Cấu hình Firebase ---
SERVICE_ACCOUNT_KEY_PATH = os.path.join(PROJECT_ROOT, 'nhandientienghetapp-firebase-adminsdk-fbsvc-ad9903830b.json')

# --- Cấu hình Server ---
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000

# --- Cấu hình UDP Server (Nhận audio từ ESP32) ---
UDP_HOST = "0.0.0.0"
UDP_PORT = 5005 # !!! Cổng này PHẢI KHỚP với cổng ESP32 gửi đến !!!
UDP_BUFFER_SIZE = 4096 # Kích thước buffer nhận UDP

# --- Cấu hình Định dạng Âm thanh (Từ ESP32) ---
# !!! QUAN TRỌNG: Đảm bảo các giá trị này khớp với cấu hình I2S trên ESP32 !!!
AUDIO_SAMPLE_RATE = 16000 # Tần số lấy mẫu (Hz)
AUDIO_BYTES_PER_SAMPLE = 4 # ESP32 dùng I2S_BITS_PER_SAMPLE_32BIT -> 32 bits = 4 bytes
AUDIO_NUM_CHANNELS = 1 # ESP32 dùng I2S_CHANNEL_FMT_ONLY_LEFT
# Định dạng dtype của numpy để giải mã ('<' = little-endian, 'i4' = signed 32-bit integer)
AUDIO_NUMPY_DTYPE = '<i4'

# --- Cấu hình Xử lý và Model ML ---
MODEL_FILENAME = "Scream_detection_Resnet34.pt" # Tên tệp model
MODEL_PATH = os.path.join(PROJECT_ROOT,'model', MODEL_FILENAME) # Đường dẫn đầy đủ đến model
# MODEL_PATH = os.path.join(PROJECT_ROOT, 'models', MODEL_FILENAME) # Nếu bạn tạo thư mục models/

# Thời gian của mỗi đoạn audio để xử lý (giây)
AUDIO_CHUNK_DURATION_S = 1.0
# Số mẫu trong mỗi đoạn chunk (tính toán tự động)
AUDIO_CHUNK_SAMPLES = int(AUDIO_SAMPLE_RATE * AUDIO_CHUNK_DURATION_S)

# Các tham số từ quá trình training model (cần khớp chính xác)
MODEL_TARGET_LENGTH_SAMPLES = 441000 # Độ dài mục tiêu để padding/cắt bớt
MODEL_N_MELS = 64
MODEL_N_FFT = 1024
MODEL_IMG_SIZE = (64, 862) # Kích thước ảnh spectrogram mong đợi bởi model
MODEL_CLASS_MAP = {0: 'Không hét', 1: 'Hét'} # Mapping từ output của model sang tên lớp
# Chọn thiết bị (GPU nếu có, nếu không thì CPU)
ML_DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

# --- Cấu hình Cảnh báo Tiếng Hét ---
SCREAM_ALERT_COOLDOWN_S = 60 # Thời gian chờ (giây) trước khi gửi lại cảnh báo từ cùng 1 IP
SCREAM_ALERT_TITLE = "Cảnh báo!"
SCREAM_ALERT_BODY_TEMPLATE = "Phát hiện tiếng hét từ thiết bị tại địa chỉ IP: {}"

# --- Cấu hình Scheduler (Nếu vẫn dùng) ---
# Ví dụ: gửi thông báo "server đang chạy" mỗi giờ
# NOTIFICATION_INTERVAL_SECONDS = 3600
# SCHEDULE_ENABLED = False # Tắt scheduler nếu không cần thông báo định kỳ nữa
SCHEDULE_ENABLED = False # Tạm thời tắt thông báo định kỳ để tập trung vào cảnh báo hét
NOTIFICATION_INTERVAL_SECONDS = 60 # Giữ lại giá trị cũ nếu SCHEDULE_ENABLED = True

# --- Cấu hình Logging ---
LOG_LEVEL = logging.INFO # Mức độ log (INFO, DEBUG, WARNING, ERROR)
LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(threadName)s] - %(module)s:%(lineno)d - %(message)s'
# LOG_TO_FILE = True
# LOG_FILENAME = 'server.log'

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
# Cấu hình thêm nếu muốn log ra file
# if LOG_TO_FILE:
#     file_handler = logging.FileHandler(LOG_FILENAME)
#     formatter = logging.Formatter(LOG_FORMAT)
#     file_handler.setFormatter(formatter)
#     logging.getLogger().addHandler(file_handler)
