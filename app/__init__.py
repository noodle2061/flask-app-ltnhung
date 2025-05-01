import flask
import logging
import threading # Cần import threading

# Import các module của ứng dụng
from . import config # Luôn import config trước
from . import token_storage
from . import firebase_client
from . import ml_handler # Import ml_handler
from . import routes
# from . import scheduler # Import scheduler ngay cả khi không dùng để tránh lỗi nếu có import vòng
from . import udp_server

def create_app():
    """Tạo và cấu hình Flask application instance."""
    app = flask.Flask(__name__)

    # --- Cấu hình Logging ---
    # Sử dụng cấu hình từ config.py đã được thực hiện ở đó
    # logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT) # Không cần gọi lại nếu config đã gọi
    app.logger.setLevel(config.LOG_LEVEL) # Đảm bảo logger của Flask cũng dùng level này
    # Ghi log vào file nếu được cấu hình trong config.py
    # if config.LOG_TO_FILE:
    #     # ... (thêm file handler như trong config)
    logging.info("--- Khởi tạo ứng dụng Flask ---")
    app.logger.info("Logger của Flask đã được cấu hình.")

    # --- Khởi tạo Firebase Admin SDK ---
    if not firebase_client.initialize_firebase():
        app.logger.error("KHÔNG THỂ KHỞI TẠO FIREBASE ADMIN SDK. KIỂM TRA LẠI TỆP KEY VÀ CẤU HÌNH.")
        # Có thể dừng ứng dụng ở đây nếu Firebase là bắt buộc
        # raise RuntimeError("Firebase initialization failed")
    else:
        app.logger.info("Firebase Admin SDK đã được khởi tạo.")

    # --- Tải Model Machine Learning ---
    app.logger.info("Đang tải model Machine Learning...")
    if not ml_handler.load_model():
         app.logger.warning("Không thể tải model ML khi khởi động. Chức năng dự đoán sẽ không hoạt động.")
         # Server vẫn có thể chạy nhưng chức năng ML sẽ bị vô hiệu hóa
    else:
         app.logger.info("Model Machine Learning đã được tải thành công.")


    # --- Đăng ký các route ---
    routes.register_routes(app)
    app.logger.info("Các route API đã được đăng ký.")

    # --- Khởi chạy các luồng nền (UDP listener và Scheduler nếu bật) ---
    # Lưu ý quan trọng về việc chạy thread trong môi trường production (Gunicorn/Waitress):
    # - Development server (app.run()) thường chạy tốt với thread.
    # - Gunicorn/Waitress có thể tạo nhiều worker process. Mỗi process sẽ cố gắng chạy các thread này.
    #   Điều này có thể dẫn đến việc UDP port bị bind nhiều lần (lỗi) hoặc scheduler chạy nhiều lần.
    # Giải pháp:
    #   1. Chỉ chạy thread trong worker chính (khó thực hiện với Gunicorn).
    #   2. Sử dụng công cụ quản lý task/process riêng biệt (Celery, Supervisor, systemd) để chạy listener và scheduler độc lập với web server.
    #   3. (Đơn giản nhất cho dự án nhỏ): Chạy Gunicorn/Waitress với CHỈ MỘT worker (`gunicorn --workers 1 run:app`).

    app.logger.info("Khởi chạy các luồng nền...")

    # Khởi chạy UDP Listener Thread
    udp_server.start_udp_thread()
    app.logger.info("Luồng UDP Listener đã bắt đầu.")

    # Khởi chạy Scheduler Thread (chỉ nếu được bật trong config)
    if config.SCHEDULE_ENABLED:
        # scheduler.start_scheduler_thread() # Hàm này cần được định nghĩa lại hoặc dùng hàm từ firebase_client
        # Sử dụng hàm gửi thông báo định kỳ từ firebase_client nếu muốn
        scheduler_thread = threading.Thread(
            target=scheduler.run_scheduler, # Hàm run_scheduler cần dùng _send_periodic_notifications_job từ firebase_client
            name="SchedulerThread",
            daemon=True
        )
        # Cần sửa lại scheduler.py để dùng hàm job từ firebase_client
        # Tạm thời comment out để tránh lỗi nếu scheduler.py chưa được sửa
        # scheduler_thread.start()
        app.logger.info("Luồng Scheduler đã bắt đầu (nếu được cấu hình đúng).")
        # LƯU Ý: Cần sửa lại file app/scheduler.py để gọi firebase_client._send_periodic_notifications_job
        # thay vì hàm job cũ nếu bạn muốn dùng lại scheduler.
    else:
         app.logger.info("Scheduler bị tắt trong cấu hình.")


    app.logger.info("--- Ứng dụng Flask đã sẵn sàng ---")
    return app
