# app/firebase_client.py
import firebase_admin
from firebase_admin import credentials, messaging, db, firestore # Thêm 'firestore'
import logging
import os
import time
import datetime # Thêm datetime

# Import cấu hình và quản lý token
from . import config
from . import token_storage # Cần truy cập token_storage để lấy danh sách token

_firebase_initialized = False
_cred = None
_db_ref = None # Tham chiếu đến root của Realtime Database (vẫn giữ nếu cần)
_firestore_db = None # <<< THÊM MỚI: Biến lưu trữ Firestore client

def initialize_firebase():
    """Khởi tạo Firebase Admin SDK cho cả FCM, RTDB (nếu cần) và Firestore."""
    global _firebase_initialized, _cred, _db_ref, _firestore_db # <<< THÊM MỚI: _firestore_db
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

        # Lấy tham chiếu đến root của Realtime database (nếu bạn vẫn dùng nó cho việc khác)
        try:
            _db_ref = db.reference()
            logging.info("Firebase Client: Realtime Database reference obtained.")
        except Exception as e:
            logging.warning(f"Firebase Client: Could not get Realtime Database reference (may not be needed): {e}")
            _db_ref = None

        # <<< THÊM MỚI: Lấy Firestore client >>>
        try:
            _firestore_db = firestore.client()
            logging.info("Firebase Client: Firestore client obtained successfully.")
        except Exception as e:
            logging.error(f"Firebase Client: Failed to get Firestore client: {e}", exc_info=True)
            # Bạn có thể quyết định dừng hẳn nếu Firestore là bắt buộc
            # return False
        # <<< KẾT THÚC THÊM MỚI >>>

        logging.info("Firebase Client: Firebase Admin SDK initialization complete.")
        return True
    except ValueError as e:
         # Bắt lỗi cụ thể nếu databaseURL không hợp lệ?
         logging.error(f"Firebase Client: Lỗi khi khởi tạo Firebase - Database URL không hợp lệ? Lỗi: {e}", exc_info=True)
         return False
    except Exception as e:
        logging.error(f"Firebase Client: Lỗi không xác định khi khởi tạo Firebase Admin SDK: {e}", exc_info=True)
        return False

# --- Hàm send_fcm_notification và send_alert_to_all giữ nguyên như cũ ---
def send_fcm_notification(token: str, title: str, body: str, data: dict = None) -> bool:
    """
    Gửi một thông báo FCM đến một token cụ thể.
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

# --- Hàm write_audio_level cho Realtime Database (giữ lại nếu vẫn cần) ---
def write_audio_level(client_ip: str, amplitude: float, timestamp: float):
    """
    Ghi giá trị biên độ âm thanh (RMS) mới nhất của một client lên Firebase Realtime Database.
    """
    if not _firebase_initialized or _db_ref is None:
        # Giảm mức log xuống DEBUG hoặc INFO vì bạn có thể không dùng RTDB nữa
        logging.debug("Firebase Client: Firebase RTDB chưa sẵn sàng, không thể ghi audio level.")
        return

    try:
        safe_client_ip = client_ip.replace('.', '-')
        path = f"audio_levels/{safe_client_ip}/latest"
        data = {
            'timestamp': timestamp,
            'amplitude': amplitude
        }
        _db_ref.child(path).set(data)
        logging.debug(f"Firebase Client: Đã ghi audio level cho {client_ip} lên RTDB: {amplitude:.3f}")

    except firebase_admin.exceptions.FirebaseError as e:
        logging.error(f"Firebase Client: Lỗi Firebase RTDB khi ghi audio level cho {client_ip}: {e}")
    except TypeError as e:
         logging.error(f"Firebase Client: Lỗi TypeError khi chuẩn bị dữ liệu RTDB cho {client_ip}: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Firebase Client: Lỗi không xác định khi ghi audio level cho {client_ip} vào RTDB: {e}", exc_info=True)

# <<< THÊM MỚI: Hàm ghi lịch sử cảnh báo vào Firestore >>>
def log_alert_to_firestore(client_ip: str, s3_key: str | None):
    """Ghi lại sự kiện cảnh báo vào collection 'alert_history' trên Firestore."""
    if not _firestore_db: # Kiểm tra xem Firestore client đã sẵn sàng chưa
        logging.warning("Firestore client not available. Cannot log alert history.")
        return

    try:
        # Chọn collection để lưu trữ. Nếu chưa có, Firestore sẽ tự tạo.
        collection_ref = _firestore_db.collection('alert_history')

        # Chuẩn bị dữ liệu cho document mới
        alert_data = {
            # Sử dụng firestore.SERVER_TIMESTAMP để Firestore tự điền thời gian phía server
            # Điều này đảm bảo thời gian nhất quán ngay cả khi đồng hồ server Python bị lệch.
            'timestamp': firestore.SERVER_TIMESTAMP,
            'client_ip': client_ip,
            # Chỉ thêm trường 's3_key' nếu nó thực sự có giá trị (không phải None)
            # Điều này giúp tiết kiệm dung lượng và làm cho dữ liệu sạch hơn.
        }
        if s3_key:
            alert_data['s3_key'] = s3_key

        # Thêm document mới vào collection. Firestore sẽ tự động tạo ID duy nhất.
        doc_ref = collection_ref.document() # Tạo tham chiếu đến document mới với ID tự sinh
        doc_ref.set(alert_data) # Ghi dữ liệu vào document đó

        # Log lại ID của document vừa tạo để tiện theo dõi (tùy chọn)
        logging.info(f"Alert for {client_ip} logged to Firestore collection 'alert_history' with ID: {doc_ref.id}")

    except Exception as e:
        # Bắt mọi lỗi có thể xảy ra trong quá trình tương tác với Firestore
        logging.error(f"Error logging alert to Firestore for IP {client_ip}: {e}", exc_info=True)
# <<< KẾT THÚC THÊM MỚI >>>

# --- Hàm _send_periodic_notifications_job giữ nguyên nếu cần ---
def _send_periodic_notifications_job():
    """Công việc gửi thông báo đến tất cả các token đã đăng ký (cho scheduler)."""
    logging.info("Firebase Client (Scheduler): Bắt đầu tác vụ gửi thông báo định kỳ...")
    title = "Thông báo định kỳ"
    body = f"Server vẫn đang chạy lúc {time.strftime('%Y-%m-%d %H:%M:%S')}"
    send_alert_to_all(title, body) # Sử dụng lại hàm send_alert_to_all
    logging.info("Firebase Client (Scheduler): Hoàn thành tác vụ gửi thông báo định kỳ.")
