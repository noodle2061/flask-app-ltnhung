from app import create_app, config,  udp_server # Import các thành phần cần thiết
import logging
# import scheduler
import signal
import sys
import time

# Tạo instance của Flask app
app = create_app()

def shutdown_server(signum, frame):
    """Hàm xử lý tín hiệu dừng server (Ctrl+C)."""
    logging.info("Nhận tín hiệu dừng (Ctrl+C)... Đang dọn dẹp.")
    # Dừng các luồng nền
    # scheduler.stop_scheduler()
    udp_server.stop_udp_listener()
    # Đợi các luồng kết thúc (tùy chọn, có thể cần join)
    logging.info("Đã yêu cầu dừng các luồng nền.")
    # Có thể thêm các thao tác dọn dẹp khác ở đây (ví dụ: đóng kết nối DB)
    logging.info("Server đang dừng.")
    # Flask development server sẽ tự dừng khi process chính thoát
    # Nếu dùng WSGI server khác, cần có cơ chế dừng riêng
    sys.exit(0)

# Đăng ký hàm xử lý tín hiệu SIGINT (Ctrl+C)
signal.signal(signal.SIGINT, shutdown_server)
# Đăng ký hàm xử lý tín hiệu SIGTERM (thường dùng bởi process manager)
signal.signal(signal.SIGTERM, shutdown_server)


if __name__ == '__main__':
    logging.info(f"Khởi chạy server trên {config.FLASK_HOST}:{config.FLASK_PORT}...")
    try:
        # Chạy Flask development server
        # Lưu ý: Không nên dùng server này cho production.
        # Hãy dùng Gunicorn hoặc Waitress.
        # Ví dụ chạy bằng Waitress: waitress-serve --host=0.0.0.0 --port=80 run:app
        app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=False, use_reloader=False)
        # debug=False và use_reloader=False quan trọng để tránh chạy các thread nhiều lần
    except OSError as e:
         logging.error(f"Lỗi khi khởi chạy Flask server trên cổng {config.FLASK_PORT}: {e}. Cổng có thể đang được sử dụng hoặc cần quyền admin (đối với cổng < 1024).")
    except Exception as e:
        logging.error(f"Lỗi không xác định khi khởi chạy Flask server: {e}", exc_info=True)
    finally:
        # Đoạn này có thể không được chạy nếu dùng sys.exit() trong signal handler
        logging.info("Server đã dừng hoàn toàn.")
