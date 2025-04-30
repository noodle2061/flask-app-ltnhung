import flask
import logging

from app import token_storage
from . import config
from . import firebase_client
from . import routes
from . import scheduler
from . import udp_server
from . import ml_handler # Import để có thể gọi load_model nếu muốn

def create_app():
    """Tạo và cấu hình Flask application instance."""
    app = flask.Flask(__name__)

    # Cấu hình logging cơ bản cho Flask (có thể tùy chỉnh thêm)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s')
    # Ghi log vào file nếu muốn
    # file_handler = logging.FileHandler('server.log')
    # file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
    # app.logger.addHandler(file_handler)
    # app.logger.setLevel(logging.INFO) # Hoặc DEBUG

    logging.info("Khởi tạo ứng dụng Flask...")
    app.logger.info("Flask logger đã được cấu hình.") # Sử dụng logger của Flask

    # Khởi tạo Firebase Admin SDK
    if not firebase_client.initialize_firebase():
        # Xử lý trường hợp không khởi tạo được Firebase (ví dụ: dừng app)
        app.logger.error("Không thể khởi tạo Firebase Admin SDK. Kiểm tra tệp serviceAccountKey.json và cấu hình.")
        # Có thể raise Exception hoặc trả về None để báo lỗi
        # raise RuntimeError("Firebase initialization failed")

    # Đăng ký các route
    routes.register_routes(app)
    app.logger.info("Các route đã được đăng ký.")

    # (Tùy chọn) Tải model ML khi khởi tạo app
    # ml_handler.load_model()

    # Khởi chạy các luồng nền (scheduler, UDP)
    # Lưu ý: Việc khởi chạy thread ở đây có thể không lý tưởng nếu dùng WSGI server
    # production. Cách tốt hơn là dùng các công cụ quản lý process/task riêng biệt.
    # Tuy nhiên, với server đơn giản này thì cách này chấp nhận được.
    scheduler.start_scheduler_thread()
    udp_server.start_udp_thread()

    app.logger.info("Ứng dụng Flask đã sẵn sàng.")
    return app

