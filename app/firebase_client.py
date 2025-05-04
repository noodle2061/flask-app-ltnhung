import firebase_admin
from firebase_admin import credentials, messaging, db # Thêm 'db'
import logging
import os
import time

# Import cấu hình và quản lý token
from . import config
from . import token_storage # Cần truy cập token_storage để lấy danh sách token

_firebase_initialized = False
_cred = None
_db_ref = None # Tham chiếu đến root của Realtime Database

def initialize_firebase():
    """Khởi tạo Firebase Admin SDK cho cả FCM và Realtime Database."""
    global _firebase_initialized, _cred, _db_ref
    if _firebase_initialized:
        logging.info("Firebase Client: Firebase Admin SDK đã được khởi tạo trước đó.")
        return True

    # Kiểm tra cả Service Account Key và Database URL
    if not os.path.exists(config.SERVICE_ACCOUNT_KEY_PATH):
        logging.error(f"Firebase Client: Lỗi: Không tìm thấy tệp Service Account Key tại '{config.SERVICE_ACCOUNT_KEY_PATH}'.")
        return False
    if not config.FIREBASE_DATABASE_URL:
        logging.error("Firebase Client: Lỗi: FIREBASE_DATABASE_URL chưa được cấu hình trong .env.")
        return False

    try:
        _cred = credentials.Certificate(config.SERVICE_ACCOUNT_KEY_PATH)
        # Thêm databaseURL vào options khi khởi tạo
        firebase_admin.initialize_app(_cred, {
            'databaseURL': config.FIREBASE_DATABASE_URL
        })
        _firebase_initialized = True
        # Lấy tham chiếu đến root của database
        _db_ref = db.reference()
        logging.info("Firebase Client: Firebase Admin SDK (FCM & Database) đã được khởi tạo thành công.")
        return True
    except ValueError as e:
         # Bắt lỗi cụ thể nếu databaseURL không hợp lệ
         logging.error(f"Firebase Client: Lỗi khi khởi tạo Firebase - Database URL không hợp lệ? Lỗi: {e}", exc_info=True)
         return False
    except Exception as e:
        logging.error(f"Firebase Client: Lỗi không xác định khi khởi tạo Firebase Admin SDK: {e}", exc_info=True)
        return False

def send_fcm_notification(token: str, title: str, body: str, data: dict = None) -> bool:
    """
    Gửi một thông báo FCM đến một token cụ thể.
    (Giữ nguyên như cũ)
    """
    if not _firebase_initialized:
        logging.warning("Firebase Client: Bỏ qua gửi FCM do Firebase Admin SDK chưa được khởi tạo.")
        return False
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            token=token,
            data=data # Thêm data payload nếu có
        )
        response = messaging.send(message)
        # Phản hồi chỉ là xác nhận đã gửi tới FCM, không đảm bảo đã đến thiết bị
        logging.debug(f"Firebase Client: Đã gửi thông báo đến token {token[:10]}... Response: {response}")
        return True
    except messaging.UnregisteredError:
        logging.warning(f"Firebase Client: Token {token[:10]}... không hợp lệ hoặc đã hủy đăng ký. Đang xóa...")
        token_storage.remove_token(token) # Xóa token không hợp lệ khỏi bộ nhớ (và DB nếu có)
        return False # Coi như gửi thất bại đối với token này
    except messaging.InvalidArgumentError:
         logging.warning(f"Firebase Client: Token {token[:10]}... không hợp lệ (Invalid Argument). Đang xóa...")
         token_storage.remove_token(token)
         return False
    except messaging.FirebaseError as e:
        # Các lỗi khác của Firebase (quota, server unavailable,...)
        logging.error(f"Firebase Client: Lỗi Firebase khi gửi FCM đến token {token[:10]}...: {e}")
        return False
    except Exception as e:
        # Các lỗi không mong muốn khác
        logging.error(f"Firebase Client: Lỗi không xác định khi gửi FCM đến token {token[:10]}...: {e}", exc_info=True)
        return False

def send_alert_to_all(title: str, body: str, data: dict = None) -> bool:
    """
    Gửi thông báo/cảnh báo đến TẤT CẢ các token đã đăng ký.
    (Giữ nguyên như cũ)
    """
    if not _firebase_initialized:
        logging.warning("Firebase Client: Không thể gửi cảnh báo vì Firebase chưa khởi tạo.")
        return False

    tokens_to_notify = token_storage.get_all_tokens()

    if not tokens_to_notify:
        logging.info("Firebase Client: Không có token nào được đăng ký để gửi cảnh báo.")
        return False

    logging.info(f"Firebase Client: Chuẩn bị gửi cảnh báo '{title}' đến {len(tokens_to_notify)} token.")

    success_count = 0
    fail_count = 0
    # Tạo một bản sao để tránh lỗi nếu danh sách bị thay đổi trong lúc lặp
    tokens_copy = list(tokens_to_notify)

    for token in tokens_copy:
        if send_fcm_notification(token, title, body, data):
            success_count += 1
        else:
            fail_count += 1
        # Thêm độ trễ nhỏ để tránh vượt rate limit của FCM nếu gửi nhiều
        time.sleep(0.05) # 50ms

    logging.info(f"Firebase Client: Hoàn thành gửi cảnh báo. Thành công: {success_count}, Thất bại/Xóa: {fail_count}")

    # Trả về True nếu ít nhất một cái thành công
    return success_count > 0

# === THÊM MỚI: Hàm ghi dữ liệu biên độ âm thanh ===
def write_audio_level(client_ip: str, amplitude: float, timestamp: float):
    """
    Ghi giá trị biên độ âm thanh (RMS) mới nhất của một client lên Firebase Realtime Database.

    Args:
        client_ip (str): Địa chỉ IP của thiết bị gửi âm thanh.
        amplitude (float): Giá trị biên độ (RMS) đã tính toán (thường trong khoảng 0-1).
        timestamp (float): Thời gian (unix timestamp) của dữ liệu.
    """
    if not _firebase_initialized or _db_ref is None:
        logging.warning("Firebase Client: Firebase DB chưa sẵn sàng, không thể ghi audio level.")
        return

    try:
        # Tạo đường dẫn động cho từng client IP
        # Thay thế dấu '.' bằng '-' vì Firebase key không cho phép '.'
        safe_client_ip = client_ip.replace('.', '-')
        path = f"audio_levels/{safe_client_ip}/latest"

        # Dữ liệu cần ghi
        data = {
            'timestamp': timestamp,
            'amplitude': amplitude
        }

        # Ghi dữ liệu lên Realtime Database (ghi đè giá trị cũ tại 'latest')
        _db_ref.child(path).set(data)
        # Log ở mức DEBUG để tránh làm đầy log
        logging.debug(f"Firebase Client: Đã ghi audio level cho {client_ip} lên DB: {amplitude:.3f}")

    except firebase_admin.exceptions.FirebaseError as e:
        logging.error(f"Firebase Client: Lỗi Firebase DB khi ghi audio level cho {client_ip}: {e}")
    except TypeError as e:
         # Có thể xảy ra nếu dữ liệu không serialize được thành JSON
         logging.error(f"Firebase Client: Lỗi TypeError khi chuẩn bị dữ liệu DB cho {client_ip}: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Firebase Client: Lỗi không xác định khi ghi audio level cho {client_ip}: {e}", exc_info=True)
# ===========================================

# Hàm gửi thông báo định kỳ (nếu cần) - giữ lại từ code gốc
def _send_periodic_notifications_job():
    """Công việc gửi thông báo đến tất cả các token đã đăng ký (cho scheduler)."""
    logging.info("Firebase Client (Scheduler): Bắt đầu tác vụ gửi thông báo định kỳ...")
    title = "Thông báo định kỳ"
    body = f"Server vẫn đang chạy lúc {time.strftime('%Y-%m-%d %H:%M:%S')}"
    send_alert_to_all(title, body) # Sử dụng lại hàm send_alert_to_all
    logging.info("Firebase Client (Scheduler): Hoàn thành tác vụ gửi thông báo định kỳ.")

