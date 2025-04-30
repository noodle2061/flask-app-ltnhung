import socket
import logging
import threading
from . import config

_stop_udp = threading.Event()

def udp_listener():
    """(Placeholder) Lắng nghe dữ liệu UDP từ ESP32."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Sử dụng địa chỉ và cổng từ config
        sock.bind((config.UDP_HOST, config.UDP_PORT))
        logging.info(f"UDP Server: Đang lắng nghe trên cổng {config.UDP_PORT}...")
        while not _stop_udp.is_set():
            try:
                # Đặt timeout để kiểm tra cờ dừng định kỳ
                sock.settimeout(1.0)
                data, addr = sock.recvfrom(1024) # buffer size
                logging.debug(f"UDP Server: Nhận được gói UDP từ {addr}: {len(data)} bytes")
                # --- TODO: Xử lý dữ liệu âm thanh `data` ở đây ---
                # Ví dụ:
                # process_audio_data(data, addr)
                # -------------------------------------------------
            except socket.timeout:
                continue # Tiếp tục vòng lặp nếu không có dữ liệu và chưa có yêu cầu dừng
            except Exception as e:
                 logging.error(f"UDP Server: Lỗi khi nhận dữ liệu: {e}", exc_info=True)

    except OSError as e:
         logging.error(f"UDP Server: Lỗi khi bind UDP port {config.UDP_PORT}: {e}. Cổng có thể đang được sử dụng.")
    except Exception as e:
        logging.error(f"UDP Server: Lỗi không xác định: {e}", exc_info=True)
    finally:
        sock.close()
        logging.info("UDP Server: Đã dừng.")

def start_udp_thread() -> threading.Thread:
    """Khởi tạo và bắt đầu luồng chạy UDP listener."""
    _stop_udp.clear()
    udp_thread = threading.Thread(target=udp_listener, name="UDPListenerThread", daemon=True)
    udp_thread.start()
    logging.info("UDP Server: Luồng đã được khởi tạo và bắt đầu.")
    return udp_thread

def stop_udp_listener():
    """Dừng UDP listener."""
    logging.info("UDP Server: Yêu cầu dừng...")
    _stop_udp.set()

