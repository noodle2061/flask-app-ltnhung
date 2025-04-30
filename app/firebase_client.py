import firebase_admin
from firebase_admin import credentials, messaging
import logging
import os

# Import cấu hình và quản lý token
from . import config
from . import token_storage

_firebase_initialized = False
_cred = None

def initialize_firebase():
    """Khởi tạo Firebase Admin SDK."""
    global _firebase_initialized, _cred
    if _firebase_initialized:
        logging.info("Firebase Admin SDK đã được khởi tạo trước đó.")
        return True

    if not os.path.exists(config.SERVICE_ACCOUNT_KEY_PATH):
        logging.error(f"Lỗi: Không tìm thấy tệp Service Account Key tại '{config.SERVICE_ACCOUNT_KEY_PATH}'.")
        logging.error("Vui lòng tải tệp key từ Firebase Console và đặt đúng đường dẫn trong config.py.")
        return False
    else:
        try:
            _cred = credentials.Certificate(config.SERVICE_ACCOUNT_KEY_PATH)
            firebase_admin.initialize_app(_cred)
            _firebase_initialized = True
            logging.info("Firebase Admin SDK đã được khởi tạo thành công.")
            return True
        except Exception as e:
            logging.error(f"Lỗi khi khởi tạo Firebase Admin SDK: {e}")
            return False

def send_fcm_notification(token: str, title: str, body: str) -> bool:
    """Gửi một thông báo FCM đến một token cụ thể."""
    if not _firebase_initialized:
        logging.warning("Bỏ qua gửi FCM do Firebase Admin SDK chưa được khởi tạo.")
        return False
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            token=token,
            # Có thể thêm data payload nếu cần
            # data={
            #     'score': '850',
            #     'time': '2:45',
            # }
        )
        response = messaging.send(message)
        logging.info(f"Đã gửi thông báo thành công đến token {token[:10]}...: {response}")
        return True
    except messaging.FirebaseError as e:
        logging.error(f"Lỗi khi gửi FCM đến token {token[:10]}...: {e}")
        # Xử lý các lỗi cụ thể (ví dụ: token không hợp lệ)
        if isinstance(e, (messaging.UnregisteredError, messaging.InvalidArgumentError)):
            logging.warning(f"Token {token[:10]}... không hợp lệ hoặc không đăng ký. Đang xóa...")
            token_storage.remove_token(token) # Xóa token không hợp lệ
        # Các lỗi khác có thể cần xử lý thêm (ví dụ: quota exceeded, server unavailable)
        return False
    except Exception as e:
        logging.error(f"Lỗi không xác định khi gửi FCM đến token {token[:10]}...: {e}")
        return False

