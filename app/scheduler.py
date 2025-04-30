import schedule
import time
import logging
import threading

from . import token_storage
from . import firebase_client
from . import config

_stop_scheduler = threading.Event() # Event để dừng vòng lặp scheduler

def _send_periodic_notifications_job():
    """Công việc gửi thông báo đến tất cả các token đã đăng ký."""
    logging.info("Scheduler: Bắt đầu tác vụ gửi thông báo định kỳ...")
    tokens_to_notify = token_storage.get_all_tokens()

    if not tokens_to_notify:
        logging.info("Scheduler: Không có token nào để gửi thông báo.")
        return

    logging.info(f"Scheduler: Sẽ gửi thông báo đến {len(tokens_to_notify)} token.")
    title = "Thông báo định kỳ"
    body = f"Đây là thông báo được gửi lúc {time.strftime('%H:%M:%S')}"

    success_count = 0
    fail_count = 0
    for token in tokens_to_notify:
        if firebase_client.send_fcm_notification(token, title, body):
            success_count += 1
        else:
            fail_count += 1
        # Thêm độ trễ nhỏ để tránh rate limiting của FCM API nếu gửi số lượng lớn
        time.sleep(0.05) # 50ms delay

    logging.info(f"Scheduler: Hoàn thành gửi thông báo. Thành công: {success_count}, Thất bại: {fail_count}")


def run_scheduler():
    """Chạy vòng lặp của scheduler trong một luồng riêng."""
    if not firebase_client._firebase_initialized:
         logging.warning("Scheduler: Firebase chưa khởi tạo, tác vụ gửi thông báo sẽ không chạy.")
         # Không cần return, schedule vẫn chạy nhưng job sẽ không làm gì nếu firebase chưa init

    logging.info(f"Scheduler: Lên lịch gửi thông báo sau mỗi {config.NOTIFICATION_INTERVAL_SECONDS} giây.")
    schedule.every(config.NOTIFICATION_INTERVAL_SECONDS).seconds.do(_send_periodic_notifications_job)

    logging.info("Scheduler: Vòng lặp bắt đầu.")
    while not _stop_scheduler.is_set():
        schedule.run_pending()
        time.sleep(1) # Kiểm tra mỗi giây
    logging.info("Scheduler: Vòng lặp đã dừng.")

def start_scheduler_thread() -> threading.Thread:
    """Khởi tạo và bắt đầu luồng chạy scheduler."""
    _stop_scheduler.clear() # Đảm bảo event được reset
    scheduler_thread = threading.Thread(target=run_scheduler, name="SchedulerThread", daemon=True)
    scheduler_thread.start()
    logging.info("Scheduler: Luồng đã được khởi tạo và bắt đầu.")
    return scheduler_thread

def stop_scheduler():
    """Dừng vòng lặp scheduler."""
    logging.info("Scheduler: Yêu cầu dừng...")
    _stop_scheduler.set()

