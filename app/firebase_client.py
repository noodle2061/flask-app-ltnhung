# app/firebase_client.py
import firebase_admin
from firebase_admin import credentials, messaging, db, firestore
# <<< BỎ IMPORT FieldPath >>>
# from google.cloud.firestore_v1.field_path import FieldPath
# <<< KẾT THÚC BỎ IMPORT >>>
import logging
import os
import time
import datetime

# Import cấu hình và quản lý token
from . import config
from . import token_storage # Cần truy cập token_storage để lấy danh sách token

_firebase_initialized = False
_cred = None
_db_ref = None # Tham chiếu đến root của Realtime Database (vẫn giữ nếu cần)
_firestore_db = None # Biến lưu trữ Firestore client

def initialize_firebase():
    """Khởi tạo Firebase Admin SDK cho cả FCM, RTDB (nếu cần) và Firestore."""
    global _firebase_initialized, _cred, _db_ref, _firestore_db
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

        # Lấy Firestore client
        try:
            _firestore_db = firestore.client()
            logging.info("Firebase Client: Firestore client obtained successfully.")
        except Exception as e:
            logging.error(f"Firebase Client: Failed to get Firestore client: {e}", exc_info=True)
            # return False # Quyết định có dừng hẳn không nếu Firestore bắt buộc
        # Kết thúc lấy Firestore client

        logging.info("Firebase Client: Firebase Admin SDK initialization complete.")
        return True
    except ValueError as e:
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
        logging.debug(f"Firebase Client: Đã gửi thông báo đến token {token[:10]}... Response: {response}")
        return True
    except messaging.UnregisteredError:
        logging.warning(f"Firebase Client: Token {token[:10]}... không hợp lệ hoặc đã hủy đăng ký. Đang xóa...")
        token_storage.remove_token(token)
        return False
    except messaging.InvalidArgumentError:
         logging.warning(f"Firebase Client: Token {token[:10]}... không hợp lệ (Invalid Argument). Đang xóa...")
         token_storage.remove_token(token)
         return False
    except messaging.FirebaseError as e:
        logging.error(f"Firebase Client: Lỗi Firebase khi gửi FCM đến token {token[:10]}...: {e}")
        return False
    except Exception as e:
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
        # <<< SỬA LỖI LOGIC: Trả về True nếu không có token, vì không có lỗi xảy ra >>>
        # return False
        return True # Không có lỗi, chỉ là không có ai để gửi
    # <<< KẾT THÚC SỬA LỖI LOGIC >>>


    logging.info(f"Firebase Client: Chuẩn bị gửi cảnh báo '{title}' đến {len(tokens_to_notify)} token.")

    success_count = 0
    fail_count = 0
    tokens_copy = list(tokens_to_notify)

    for token in tokens_copy:
        if send_fcm_notification(token, title, body, data):
            success_count += 1
        else:
            fail_count += 1
        time.sleep(0.05) # 50ms

    logging.info(f"Firebase Client: Hoàn thành gửi cảnh báo. Thành công: {success_count}, Thất bại/Xóa: {fail_count}")
    # <<< SỬA LỖI LOGIC: Trả về True nếu không có lỗi (ngay cả khi không gửi được cái nào) >>>
    # Trả về False chỉ khi có lỗi nghiêm trọng xảy ra trong quá trình gửi
    # return success_count > 0
    return True # Giả định không có lỗi nghiêm trọng xảy ra trong vòng lặp
    # <<< KẾT THÚC SỬA LỖI LOGIC >>>


# --- Hàm write_audio_level cho Realtime Database (giữ lại nếu vẫn cần) ---
def write_audio_level(client_ip: str, amplitude: float, timestamp: float):
    """
    Ghi giá trị biên độ âm thanh (RMS) mới nhất của một client lên Firebase Realtime Database.
    """
    if not _firebase_initialized or _db_ref is None:
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

# --- Hàm ghi lịch sử cảnh báo vào Firestore ---
def log_alert_to_firestore(client_ip: str, s3_key: str | None):
    """Ghi lại sự kiện cảnh báo vào collection 'alert_history' trên Firestore."""
    if not _firestore_db:
        logging.warning("Firestore client not available. Cannot log alert history.")
        return

    try:
        collection_ref = _firestore_db.collection('alert_history')
        alert_data = {
            'timestamp': firestore.SERVER_TIMESTAMP,
            'client_ip': client_ip,
        }
        if s3_key:
            # <<< SỬA ĐỔI: Dùng tên trường 's3_key' (gạch dưới) nếu bạn đã đổi trong Firestore >>>
            # Nếu chưa đổi thì giữ nguyên 's3_key'
            alert_data['s3_key'] = s3_key # Giả sử bạn chưa đổi tên trường này
            # alert_data['s3_key'] = s3_key # Nếu đã đổi thành s3_key

        doc_ref = collection_ref.document()
        doc_ref.set(alert_data)
        logging.info(f"Alert for {client_ip} logged to Firestore collection 'alert_history' with ID: {doc_ref.id}")

    except Exception as e:
        logging.error(f"Error logging alert to Firestore for IP {client_ip}: {e}", exc_info=True)

# --- Hàm _send_periodic_notifications_job giữ nguyên nếu cần ---
def _send_periodic_notifications_job():
    """Công việc gửi thông báo đến tất cả các token đã đăng ký (cho scheduler)."""
    logging.info("Firebase Client (Scheduler): Bắt đầu tác vụ gửi thông báo định kỳ...")
    title = "Thông báo định kỳ"
    body = f"Server vẫn đang chạy lúc {time.strftime('%Y-%m-%d %H:%M:%S')}"
    send_alert_to_all(title, body)
    logging.info("Firebase Client (Scheduler): Hoàn thành tác vụ gửi thông báo định kỳ.")


# ==============================================================================
# <<< SỬA ĐỔI HÀM LẤY SỐ ĐIỆN THOẠI KHẨN CẤP MẶC ĐỊNH >>>
# ==============================================================================
def get_default_emergency_contact() -> str | None:
    """
    Truy vấn Firestore để lấy số điện thoại từ liên hệ khẩn cấp mặc định.

    Returns:
        str: Số điện thoại (ví dụ: "+84123456789") nếu tìm thấy.
        None: Nếu không tìm thấy liên hệ mặc định hoặc có lỗi.
    """
    if not _firestore_db:
        logging.error("Firestore client not available. Cannot get emergency contact.")
        return None

    try:
        # Tham chiếu đến collection 'emergency_contacts'
        contacts_ref = _firestore_db.collection('emergency_contacts')

        # <<< SỬA ĐỔI: Truy vấn trực tiếp bằng tên trường có dấu gạch dưới >>>
        # Tạo truy vấn để tìm document có trường 'is_default' bằng true
        query = contacts_ref.where(filter=firestore.FieldFilter('is_default', '==', True)).limit(1)
        # <<< KẾT THÚC SỬA ĐỔI >>>

        # Thực thi truy vấn
        results = query.stream()

        # Lấy document đầu tiên (và duy nhất nếu cấu hình đúng)
        default_contact_doc = next(results, None)

        if default_contact_doc:
            contact_data = default_contact_doc.to_dict()
            # <<< SỬA ĐỔI: Lấy số điện thoại từ trường 'phone_number' (gạch dưới) >>>
            phone_number = contact_data.get('phone_number')

            if phone_number and isinstance(phone_number, str):
                logging.info(f"Found default emergency phone number: {phone_number}")
                return phone_number
            else:
                logging.error(f"Default contact found (ID: {default_contact_doc.id}) but 'phone_number' field is missing, empty, or not a string.")
                return None
        else:
            logging.warning("No default emergency contact (is_default == true) found in 'emergency_contacts' collection.")
            return None

    except Exception as e:
        # Ghi log lỗi cụ thể hơn
        logging.error(f"Error querying Firestore for default emergency contact: {e}", exc_info=True)
        return None
# ==============================================================================
# <<< KẾT THÚC SỬA ĐỔI HÀM >>>
# ==============================================================================
