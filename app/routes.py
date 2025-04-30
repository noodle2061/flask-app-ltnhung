from flask import request, jsonify, current_app
import logging

from . import token_storage

def register_routes(app):
    """Đăng ký các route cho Flask app."""

    @app.route('/register_token', methods=['POST'])
    def handle_register_token():
        """
        Endpoint nhận FCM token từ ứng dụng Android.
        Dữ liệu mong đợi ở dạng JSON: {"token": "your_fcm_token"}
        """
        try:
            data = request.get_json()
            if not data or 'token' not in data:
                logging.warning("Route /register_token: Thiếu trường 'token' trong JSON.")
                return jsonify({"status": "error", "message": "Missing 'token' in JSON payload"}), 400

            token = data['token']

            # Gọi hàm từ module token_storage để thêm token
            token_storage.add_token(token)

            return jsonify({"status": "success", "message": "Token registered"}), 200

        except Exception as e:
            logging.error(f"Lỗi khi xử lý route /register_token: {e}", exc_info=True)
            # current_app.logger.error(f"Error processing /register_token: {e}", exc_info=True) # Cách log khác của Flask
            return jsonify({"status": "error", "message": "Internal server error"}), 500

    @app.route('/health', methods=['GET'])
    def health_check():
        """Endpoint kiểm tra sức khỏe đơn giản."""
        return jsonify({"status": "ok"}), 200

    # Thêm các route khác ở đây nếu cần (ví dụ: /predict)

