import firebase_admin
from firebase_admin import credentials, messaging
import logging
import os
import time # Thêm import time

# Import cấu hình và quản lý token
from . import config
from . import token_storage # Cần truy cập token_storage để lấy danh sách token

_firebase_initialized = False
_cred = None

def initialize_firebase():
    """Khởi tạo Firebase Admin SDK."""
    global _firebase_initialized, _cred
    if _firebase_initialized:
        logging.info("Firebase Client: Firebase Admin SDK đã được khởi tạo trước đó.")
        return True

    if not os.path.exists(config.SERVICE_ACCOUNT_KEY_PATH):
        logging.error(f"Firebase Client: Lỗi: Không tìm thấy tệp Service Account Key tại '{config.SERVICE_ACCOUNT_KEY_PATH}'.")
        logging.error("Firebase Client: Vui lòng tải tệp key từ Firebase Console và đặt đúng đường dẫn trong config.py.")
        return False
    else:
        try:
            _cred = credentials.Certificate(config.SERVICE_ACCOUNT_KEY_PATH)
            firebase_admin.initialize_app(_cred)
            _firebase_initialized = True
            logging.info("Firebase Client: Firebase Admin SDK đã được khởi tạo thành công.")
            return True
        except Exception as e:
            logging.error(f"Firebase Client: Lỗi khi khởi tạo Firebase Admin SDK: {e}", exc_info=True)
            return False

def send_fcm_notification(token: str, title: str, body: str, data: dict = None) -> bool:
    """
    Gửi một thông báo FCM đến một token cụ thể.
    Args:
        token (str): FCM token của thiết bị nhận.
        title (str): Tiêu đề của thông báo.
        body (str): Nội dung của thông báo.
        data (dict, optional): Dữ liệu payload tùy chỉnh. Defaults to None.
    Returns:
        bool: True nếu gửi thành công (hoặc được FCM chấp nhận), False nếu có lỗi.
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
    Args:
        title (str): Tiêu đề cảnh báo.
        body (str): Nội dung cảnh báo.
        data (dict, optional): Dữ liệu payload tùy chỉnh (ví dụ: loại cảnh báo, timestamp).
    Returns:
        bool: True nếu ít nhất một thông báo được gửi thành công, False nếu không có token hoặc tất cả đều lỗi.
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

# Hàm gửi thông báo định kỳ (nếu cần) - giữ lại từ code gốc
def _send_periodic_notifications_job():
    """Công việc gửi thông báo đến tất cả các token đã đăng ký (cho scheduler)."""
    logging.info("Firebase Client (Scheduler): Bắt đầu tác vụ gửi thông báo định kỳ...")
    title = "Thông báo định kỳ"
    body = f"Server vẫn đang chạy lúc {time.strftime('%Y-%m-%d %H:%M:%S')}"
    # Có thể thêm data payload nếu muốn
    # data = {"type": "periodic_check", "timestamp": str(time.time())}
    send_alert_to_all(title, body) # Sử dụng lại hàm send_alert_to_all
    logging.info("Firebase Client (Scheduler): Hoàn thành tác vụ gửi thông báo định kỳ.")

