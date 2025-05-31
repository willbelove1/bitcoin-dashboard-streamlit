import pandas as pd
import numpy as np
from pycoingecko import CoinGeckoAPI
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator
from ta.volatility import BollingerBands
try:
    from plyer import notification
except ImportError:
    notification = None
    print("Warning: plyer not installed. Desktop notifications will be disabled.")
from datetime import datetime, timedelta
from binance.client import Client
import os
from dotenv import load_dotenv
import requests
import json
import backtrader as bt
import logging
import warnings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st
import threading
import re
import toml
import schedule
import time

# Bỏ warning ta.trend
warnings.filterwarnings("ignore", category=RuntimeWarning, module="ta.trend")

# Thiết lập logging
logging.basicConfig(
    filename='C:\\Users\\HP\\Downloads\\bitcoin tool\\bitcoin_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Tải biến môi trường
env_path = 'C:\\Users\\HP\\Downloads\\bitcoin tool\\.env'
secrets_path = 'C:\\Users\\HP\\Downloads\\bitcoin tool\\secrets.toml'

# Ưu tiên secrets.toml, fallback về .env
if os.path.exists(secrets_path):
    try:
        with open(secrets_path, 'r') as f:
            config = toml.load(f)
            secrets = config.get('secrets', {})
            for key, value in secrets.items():
                os.environ[key] = str(value)
        logging.info(f"Đã đọc biến môi trường từ {secrets_path}")
    except Exception as e:
        logging.error(f"Lỗi đọc secrets.toml: {str(e)}")
        st.error(f"Lỗi đọc secrets.toml: {str(e)}. Thử đọc .env.")
        load_dotenv(env_path)
else:
    load_dotenv(env_path)
    logging.info(f"Không tìm thấy secrets.toml, đọc từ {env_path}")

BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
BINANCE_API_SECRET = os.getenv('BINANCE_API_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Khởi tạo CoinGecko API
cg = CoinGeckoAPI()

# Map coin với symbol API
COIN_CONFIG = {
    'BTC': {'bybit': 'BTCUSDT', 'binance': 'BTCUSDT', 'coingecko': 'bitcoin'},
    'ETH': {'bybit': 'ETHUSDT', 'binance': 'ETHUSDT', 'coingecko': 'ethereum'},
    'BNB': {'bybit': 'BNBUSDT', 'binance': 'BNBUSDT', 'coingecko': 'binancecoin'},
    'ADA': {'bybit': 'ADAUSDT', 'binance': 'ADAUSDT', 'coingecko': 'cardano'},
    'SOL': {'bybit': 'SOLUSDT', 'binance': 'SOLUSDT', 'coingecko': 'solana'},
    'SUI': {'bybit': 'SUIUSDT', 'binance': 'SUIUSDT', 'coingecko': 'sui'},
    'PI': {'bybit': None, 'binance': None, 'coingecko': 'pi-network'}
}

# Hàm tự động gửi Telegram
def auto_send_telegram(coin='BTC'):
    logging.info(f"Tự động phân tích {coin} lúc {datetime.now()}")
    crypto_data, fib_levels, signal_output, message, chart_path = analyze_crypto(coin)
    if message and signal_output:
        send_telegram_message(
            TELEGRAM_TOKEN,
            TELEGRAM_CHAT_ID,
            message,
            signal_output.split("### AI Strategy")[1],
            chart_path
        )
        logging.info(f"Tự động gửi Telegram cho {coin} thành công")
    else:
        logging.error(f"Lỗi tự động gửi Telegram cho {coin}: Không có tín hiệu")
        st.error(f"Lỗi tự động gửi Telegram cho {coin}: Không có tín hiệu")

# Hàm chạy scheduler
def run_scheduler():
    # Lên lịch gửi lúc 8h sáng (Asia/Ho_Chi_Minh, UTC+7)
    schedule.every().day.at("08:00", tz="Asia/Ho_Chi_Minh").do(auto_send_telegram, coin='BTC')
    while True:
        schedule.run_pending()
        time.sleep(60)  # Kiểm tra mỗi phút

# Test Telegram API
def test_telegram(token, chat_id):
    if not token:
        error_msg = "Lỗi: Thiếu TELEGRAM_TOKEN trong .env hoặc secrets.toml"
        logging.error(error_msg)
        st.error(error_msg)
        return False
    if not chat_id:
        error_msg = "Lỗi: Thiếu TELEGRAM_CHAT_ID trong .env hoặc secrets.toml"
        logging.error(error_msg)
        st.error(error_msg)
        return False

    test_message = "Test Crypto Tool: Kiểm tra Telegram API thành công!"
    logging.info(f"Test Telegram: chat_id={chat_id}, message={test_message}")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': test_message}
    try:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=5, status_forcelist=[429, 500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        response = session.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info("Test Telegram thành công")
        st.success("Test Telegram thành công! Kiểm tra tin nhắn trong Telegram.")
        return True
    except Exception as e:
        error_msg = f"Lỗi test Telegram: {str(e)}"
        logging.error(error_msg)
        st.error(error_msg)
        return False

# Lấy dữ liệu crypto
def fetch_crypto_data(coin, vs_currency='usd', days=60):
    logging.info(f"Thread: {threading.current_thread().name}, Coin: {coin}")
    df = pd.DataFrame()
    config = COIN_CONFIG.get(coin, {})

    # 1. Thử Bybit API (public, không cần key)
    if config.get('bybit'):
        try:
            url = "https://api.bybit.com/v5/market/kline"
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
            params = {
                'category': 'spot',
                'symbol': config['bybit'],
                'interval': 'D',
                'start': start_time,
                'end': end_time,
                'limit': 1000
            }
            session = requests.Session()
            retries = Retry(total=3, backoff_factor=5, status_forcelist=[429, 500, 502, 503, 504])
            session.mount('https://', HTTPAdapter(max_retries=retries))
            response = session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                klines = data['result']['list']
                df_bybit = pd.DataFrame(klines, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
                ])
                df_bybit['timestamp'] = df_bybit['timestamp'].astype(int)
                df_bybit['date'] = pd.to_datetime(df_bybit['timestamp'], unit='ms')
                df_bybit.set_index('date', inplace=True)
                df_bybit = df_bybit[['open', 'high', 'low', 'close', 'volume']].astype(float)
                df_bybit['price'] = df_bybit['close']
                df = df_bybit.sort_index()
                logging.info(f"Lấy dữ liệu {coin} từ Bybit thành công")
        except Exception as e:
            logging.error(f"Lỗi Bybit API cho {coin}: {str(e)}")
            st.error(f"Lỗi Bybit API cho {coin}: {str(e)}. Thử Binance.")

    # 2. Thử Binance API nếu Bybit thất bại
    if (df.empty or len(df) < days) and config.get('binance'):
        if BINANCE_API_KEY and BINANCE_API_SECRET:
            try:
                binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
                klines = binance_client.get_historical_klines(config['binance'], Client.KLINE_INTERVAL_1DAY, f"{days} days ago UTC")
                df_binance = pd.DataFrame(klines, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_asset_volume', 'number_of_trades',
                    'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                ])
                df_binance['date'] = pd.to_datetime(df_binance['timestamp'], unit='ms')
                df_binance.set_index('date', inplace=True)
                df_binance = df_binance[['open', 'high', 'low', 'close', 'volume']].astype(float)
                df_binance['price'] = df_binance['close']
                df = df_binance
                logging.info(f"Lấy dữ liệu {coin} từ Binance thành công")
                try:
                    binance_client.close_connection()
                except Exception as e:
                    logging.warning(f"Lỗi đóng Binance client: {e}")
            except Exception as e:
                logging.error(f"Lỗi Binance API cho {coin}: {str(e)}")
                st.error(f"Lỗi Binance API cho {coin}: {str(e)}. Thử CoinGecko.")

    # 3. Fallback CoinGecko
    if df.empty or len(df) < days:
        try:
            data = cg.get_coin_market_chart_by_id(id=config['coingecko'], vs_currency=vs_currency, days=days)
            prices = data['prices']
            df_cg = pd.DataFrame(prices, columns=['timestamp', 'price'])
            df_cg['date'] = pd.to_datetime(df_cg['timestamp'], unit='ms')
            df_cg.set_index('date', inplace=True)
            df_cg.drop(['timestamp'], axis=1, inplace=True)
            df_cg['open'] = df_cg['high'] = df_cg['low'] = df_cg['close'] = df_cg['price']
            df_cg['volume'] = 0
            df = df_cg
            logging.info(f"Lấy dữ liệu {coin} từ CoinGecko thành công")
        except Exception as e:
            logging.error(f"Lỗi CoinGecko API cho {coin}: {str(e)}")
            st.error(f"Lỗi CoinGecko API cho {coin}: {str(e)}")
            return pd.DataFrame()

    df.ffill(inplace=True)
    logging.info(f"Dữ liệu {coin}: {df[['price', 'open', 'high', 'low', 'close']].tail(1).to_dict()}")
    logging.info(f"Giá min: {df['price'].min()}, max: {df['price'].max()}")
    return df

# Tính Fibonacci
def calculate_fibonacci_levels(df):
    high_price = df['high'].max()
    low_price = df['low'].min()
    diff = high_price - low_price
    levels = {
        'fib_0.0': low_price,
        'fib_0.236': low_price + diff * 0.236,
        'fib_0.382': low_price + diff * 0.382,
        'fib_0.5': low_price + diff * 0.5,
        'fib_0.618': low_price + diff * 0.618,
        'fib_0.786': low_price + diff * 0.786,
        'fib_1.0': high_price
    }
    return levels

# Kiểm tra giá gần Fibonacci
def is_near_fib_level(price, fib_levels, tolerance=0.01):
    for level_name, level_price in fib_levels.items():
        if abs(price - level_price) / price < tolerance:
            return level_name
    return None

# Lấy hỗ trợ/kháng cự
def get_support_resistance(df, fib_levels):
    logging.info(f"Fib levels: {fib_levels}")
    support = min([fib_levels['fib_0.236'], fib_levels['fib_0.382'], fib_levels['fib_0.5']])
    resistance = max([fib_levels['fib_0.618'], fib_levels['fib_0.786'], fib_levels['fib_1.0']])
    logging.info(f"Support: {support}, Resistance: {resistance}")
    return support, resistance

# Xác định xu hướng
def get_trend(latest_data):
    if latest_data['macd'] > latest_data['macd_signal'] and latest_data['adx'] > 25:
        return "Tăng"
    elif latest_data['macd'] < latest_data['macd_signal'] and latest_data['adx'] > 25:
        return "Giảm"
    return "Đi ngang"

# Gọi Gemini AI
def get_gemini_recommendation(latest_data, fib_level, support, resistance, coin):
    if not GEMINI_API_KEY:
        logging.warning("Thiếu GEMINI_API_KEY")
        st.warning("Thiếu GEMINI_API_KEY")
        return {'strategy': []}
    
    adx_value = f"{latest_data['adx']:.2f}" if not np.isnan(latest_data['adx']) else 'N/A'
    trend = get_trend(latest_data)
    price = latest_data['price']
    rsi = latest_data['rsi']
    macd = latest_data['macd']
    macd_signal = latest_data['macd_signal']
    bb_high = latest_data['bb_high']
    bb_low = latest_data['bb_low']
    volume = latest_data['volume']
    pattern = ('Giá gần Bollinger thấp, có thể tăng' if price < bb_low else 
               'Giá gần Bollinger cao, có thể giảm' if price > bb_high else 
               'Không có mô hình')
    
    prompt = f"""
    Dựa trên biểu đồ kỹ thuật {coin} hiện tại gồm các chỉ số:
    - Giá: {price:,.2f}
    - RSI: {rsi:.1f}
    - MACD: {macd:.0f}, Signal: {macd_signal:.0f} (MACD {'đang tăng' if macd > macd_signal else 'đang giảm'})
    - Bollinger Band: Cao {bb_high:,.0f} / Thấp {bb_low:,.0f}
    - ADX: {adx_value} ({">25, xu hướng mạnh" if float(adx_value) > 25 else "<25, xu hướng yếu"})
    - Vị trí Fib: {fib_level or 'N/A'}

    Hãy phân tích và cập nhật lại chiến lược đầu tư AI theo bảng:
    Kịch bản | Điều kiện xác nhận | Hành động | Mục tiêu / Gợi ý quản lý

    Trong đó cần lưu ý:
    - Không khuyến nghị mua nếu ADX < 25 trừ khi có breakout mạnh.
    - Phân biệt rõ vùng Hold (Sideways) để tránh mở lệnh không cần thiết.
    - Gắn mục tiêu chốt lời và dừng lỗ hợp lý theo Fibonacci và vùng giá kỹ thuật.
    - Dùng ngôn ngữ rõ ràng, chính xác và dễ hiểu cho nhà đầu tư cá nhân.
    - Gợi ý tín hiệu vào lệnh mua/bán/giữ khi đến 1 mức giá nhất định.

    Ví dụ:
    - Nếu giá vượt kháng cự {resistance:.1f} và ADX > 25 thì nên cân nhắc Mở Long.
    - Nếu giá thủng hỗ trợ {support:.1f} và MACD < Signal thì nên Mở Short.
    - Nếu giá nằm trong vùng {support:.1f}–{resistance:.1f} và ADX < 25 thì nên Hold.
    - Gợi ý đặt mục tiêu lãi khi Long: {resistance+1000:.0f} hoặc {resistance+2000:.0f}
    - Gợi ý chốt Short: {support-1000:.0f}, {support-2000:.0f}

    Xu hướng hiện tại: {trend}
    Mô hình kỹ thuật: {pattern}

    Hãy trả kết quả dưới dạng JSON chuẩn:
    {{
        "strategy": [
            {{
                "scenario": "Buy",
                "confirmation": "Giá vượt {resistance:.1f} kèm ADX > 25",
                "action": "Mở Long",
                "target": [{resistance + 1000:.0f}, {resistance + 2000:.0f}]
            }},
            {{
                "scenario": "Sell",
                "confirmation": "Giá thủng {support:.1f} và MACD < Signal",
                "action": "Mở Short",
                "target": [{support - 1000:.0f}, {support - 2000:.0f}]
            }},
            {{
                "scenario": "Hold",
                "confirmation": "Giá dao động trong vùng {support:.1f}–{resistance:.1f}, ADX < 25",
                "action": "Hold",
                "target": []
            }}
        ]
    }}
    """
    
    try:
        headers = {'Content-Type': 'application/json'}
        payload = {
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'response_mime_type': 'application/json'}
        }
        
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=5, status_forcelist=[429, 500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        response = session.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}',
            json=payload, headers=headers, timeout=10
        )
        response.raise_for_status()
        result = response.json()
        content = result['candidates'][0]['content']['parts'][0]['text']
        return json.loads(content)
    except Exception as e:
        logging.error(f"Lỗi Gemini API cho {coin}: {str(e)}")
        st.error(f"Lỗi Gemini API cho {coin}: {str(e)}")
        return {
            'strategy': [
                {'scenario': 'Buy', 'confirmation': f"Giá vượt {resistance:.1f} kèm ADX > 25", 'action': 'Mở Long', 'target': [resistance + 1000, resistance + 2000]},
                {'scenario': 'Sell', 'confirmation': f"Giá thủng {support:.1f} và MACD < Signal", 'action': 'Mở Short', 'target': [support - 1000, support - 2000]},
                {'scenario': 'Hold', 'confirmation': f"Giá dao động trong vùng {support:.1f}–{resistance:.1f}, ADX < 25", 'action': 'Hold', 'target': []}
            ]
        }

# Tính chỉ báo kỹ thuật
def calculate_indicators(df):
    df['rsi'] = RSIIndicator(close=df['price'], window=14).rsi()
    macd = MACD(close=df['price'])
    df['macd'] = macd.macd().fillna(0)
    df['macd_signal'] = macd.macd_signal().fillna(0)
    df['macd_diff'] = macd.macd_diff().fillna(0)
    bb = BollingerBands(close=df['price'], window=20)
    df['bb_high'] = bb.bollinger_hband()
    df['bb_low'] = bb.bollinger_lband()
    df['bb_mid'] = bb.bollinger_mavg()
    atr = (df['high'].rolling(window=14).max() - df['low'].rolling(window=14).min()).replace(0, np.nan)
    df['adx'] = np.where(atr.notna(), ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14).adx(), 20)
    df['adx'] = df['adx'].fillna(20)
    return df

# Tạo tín hiệu
def generate_signals(df, fib_levels, coin):
    df['signal'] = 'Hold'
    df.loc[df['rsi'] > 70, 'rsi_signal'] = 'Sell'
    df.loc[df['rsi'] < 30, 'rsi_signal'] = 'Buy'
    df.loc[(df['rsi'] >= 30) & (df['rsi'] <= 70), 'rsi_signal'] = 'Hold'
    df['macd_signal_text'] = 'Hold'
    df.loc[df['macd'] > df['macd_signal'], 'macd_signal_text'] = 'Buy'
    df.loc[df['macd'] < df['macd_signal'], 'macd_signal_text'] = 'Sell'
    df['bb_signal'] = 'Hold'
    df.loc[df['price'] > df['bb_high'], 'bb_signal'] = 'Sell'
    df.loc[df['price'] < df['bb_low'], 'bb_signal'] = 'Buy'
    df['fib_signal'] = 'Hold'
    for index, row in df.iterrows():
        fib_level = is_near_fib_level(row['price'], fib_levels)
        if fib_level in ['fib_0.236', 'fib_0.382', 'fib_0.5']:
            df.at[index, 'fib_signal'] = 'Buy'
        elif fib_level in ['fib_0.618', 'fib_0.786', 'fib_1.0']:
            df.at[index, 'fib_signal'] = 'Sell'
    df['gemini_signal'] = 'N/A'
    df['gemini_reason'] = 'AI Strategy'
    latest = df.iloc[-1]
    fib_level = is_near_fib_level(latest['price'], fib_levels)
    support, resistance = get_support_resistance(df, fib_levels)
    gemini_result = get_gemini_recommendation(latest, fib_level, support, resistance, coin)
    df.at[df.index[-1], 'gemini_signal'] = '; '.join([f"{s['scenario']}: {s['action']}" for s in gemini_result['strategy']])
    
    df['buy_signal_count'] = (
        (df['rsi_signal'] == 'Buy').astype(int) +
        (df['macd_signal_text'] == 'Buy').astype(int) +
        (df['bb_signal'] == 'Buy').astype(int) +
        (df['fib_signal'] == 'Buy').astype(int)
    )
    df['sell_signal_count'] = (
        (df['rsi_signal'] == 'Sell').astype(int) +
        (df['macd_signal_text'] == 'Sell').astype(int) +
        (df['bb_signal'] == 'Sell').astype(int) +
        (df['fib_signal'] == 'Sell').astype(int)
    )
    df.loc[(df['buy_signal_count'] >= 2) & (df['buy_signal_count'] > df['sell_signal_count']), 'signal'] = 'Long'
    df.loc[(df['sell_signal_count'] >= 2) & (df['sell_signal_count'] > df['buy_signal_count']) & (df['adx'] > 25), 'signal'] = 'Short'
    
    logging.info(f"Tín hiệu {coin}: {df['signal'].iloc[-1]}, Buy: {df['buy_signal_count'].iloc[-1]}, Sell: {df['sell_signal_count'].iloc[-1]}")
    logging.info(f"RSI: {df['rsi_signal'].iloc[-1]}, MACD: {df['macd_signal_text'].iloc[-1]}, BB: {df['bb_signal'].iloc[-1]}, Fib: {df['fib_signal'].iloc[-1]}")
    logging.info(f"Dữ liệu cuối {coin}: {df[['rsi', 'macd', 'macd_signal', 'bb_high', 'bb_low', 'adx', 'signal']].tail(1).to_dict()}")
    return df, gemini_result

# Thông báo desktop
def send_notification(signal, price, rsi, macd, macd_signal, bb_high, bb_low, adx, fib_level, gemini_signal, gemini_reason, coin):
    if signal in ['Long', 'Short']:
        signal_vn = 'Mua' if signal == 'Long' else 'Bán'
        adx_value = f"{adx:.2f}" if not np.isnan(adx) else 'N/A'
        message = (
            f"Tín hiệu: {signal_vn} {coin}\n"
            f"Giá: ${price:,.2f}\n"
            f"RSI: {rsi:.1f}\n"
            f"MACD: {macd:.0f}\n"
            f"BB: ${bb_high:,.0f}/${bb_low:,.0f}\n"
            f"ADX: {adx_value}"
        )[:200]
        if notification:
            try:
                notification.notify(
                    title=f"{coin} Signal",
                    message=message,
                    app_name="Crypto Tool",
                    timeout=10
                )
                logging.info(f"Thông báo: {signal_vn} {coin}")
            except Exception as e:
                logging.error(f"Lỗi thông báo {coin}: {str(e)}")
        else:
            logging.warning(f"Không gửi thông báo desktop cho {coin} vì thiếu plyer")

# Thông báo Telegram
def send_telegram_message(token, chat_id, message, strategy_output, chart_path):
    if not token:
        error_msg = "Lỗi: Thiếu TELEGRAM_TOKEN trong .env hoặc secrets.toml"
        logging.error(error_msg)
        st.error(error_msg)
        return False
    if not chat_id:
        error_msg = "Lỗi: Thiếu TELEGRAM_CHAT_ID trong .env hoặc secrets.toml"
        logging.error(error_msg)
        st.error(error_msg)
        return False

    # Thêm AI strategy vào tin nhắn
    strategy_lines = strategy_output.split('\n')[2:-1]  # Bỏ header và divider
    strategy_text = "AI Strategy:\n" + '\n'.join(strategy_lines[:3])  # Giới hạn 3 dòng
    full_message = f"{message}\n\n{strategy_text}"
    
    # Loại bỏ ký tự đặc biệt
    full_message = re.sub(r'[^\w\s\$\.\,\:\;\-\|]', '', full_message)
    
    # Rút gọn nếu vượt 4000 ký tự
    if len(full_message) > 4000:
        full_message = full_message[:3997] + "..."
    
    logging.info(f"Gửi Telegram: chat_id={chat_id}, message={full_message}")
    
    # Gửi tin nhắn
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': full_message}
    try:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=5, status_forcelist=[429, 500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        response = session.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info("Gửi Telegram tin nhắn thành công")
    except Exception as e:
        error_msg = f"Lỗi gửi Telegram tin nhắn: {str(e)}"
        logging.error(error_msg)
        st.error(error_msg)
        return False

    # Gửi ảnh chart
    if chart_path and os.path.exists(chart_path):
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open(chart_path, 'rb') as photo:
            files = {'photo': photo}
            payload = {'chat_id': chat_id, 'caption': 'Biểu đồ giá'}
            try:
                response = session.post(url, data=payload, files=files, timeout=10)
                response.raise_for_status()
                logging.info("Gửi Telegram ảnh chart thành công")
            except Exception as e:
                error_msg = f"Lỗi gửi Telegram ảnh: {str(e)}"
                logging.error(error_msg)
                st.error(error_msg)
                return False
    
    st.success("Gửi Telegram thành công!")
    return True

# Vẽ biểu đồ
def plot_data(df, fib_levels, coin):
    try:
        plt.close('all')
        fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
        ax1.plot(df.index, df['price'], label='Giá', color='blue')
        ax1.plot(df.index, df['bb_high'], label='BB Cao', color='green', alpha=0.5)
        ax1.plot(df.index, df['bb_mid'], label='BB Mid', color='orange', alpha=0.5)
        ax1.plot(df.index, df['bb_low'], label='BB Thấp', color='red', alpha=0.5)
        for level_name, level_price in fib_levels.items():
            ax1.axhline(level_price, label=f'Fib {level_name}', linestyle='--', alpha=0.3)
        ax1.set_title(f'Giá {coin} và Bollinger')
        ax1.set_ylabel('Giá (USD)')
        ax1.legend()
        ax1.grid()
        ax2.plot(df.index, df['rsi'], label='RSI', color='purple')
        ax2.axhline(70, color='red', linestyle='--', alpha=0.5)
        ax2.axhline(30, color='green', linestyle='--', alpha=0.5)
        ax2.set_title('RSI')
        ax2.set_ylabel('Value')
        ax2.legend()
        ax2.grid()
        ax3.plot(df.index, df['macd'], label='MACD', color='blue')
        ax3.plot(df.index, df['macd_signal'], label='Signal', color='orange')
        ax3.bar(df.index, df['macd_diff'], label='MACD Diff', color='gray', alpha=0.3)
        ax3.set_title('MACD')
        ax3.set_ylabel('MACD')
        ax3.legend()
        ax3.grid()
        ax4.plot(df.index, df['adx'], label='ADX', color='blue')
        ax4.axhline(25, color='green', linestyle='--', alpha=0.5)
        ax4.set_title('ADX')
        ax4.set_ylabel('ADX')
        ax4.legend()
        ax4.grid()
        latest = df.iloc[-1]
        plt.figtext(0.99, 0.01, f"AI: {latest['gemini_signal'][:50]}", horizontalalignment='right', fontsize=8)
        plt.tight_layout()
        chart_path = f'C:\\Users\\HP\\Downloads\\bitcoin tool\\{coin.lower()}_chart.png'
        plt.savefig(chart_path)
        plt.close()
        logging.info(f"Lưu biểu đồ {coin} tại {chart_path}")
        return chart_path
    except Exception as e:
        logging.error(f"Lỗi lưu biểu đồ {coin}: {str(e)}, Shape: {df.shape}")
        st.error(f"Lỗi lưu biểu đồ {coin}: {str(e)}")
        return None

# In tín hiệu
def get_latest_signal(df, fib_levels, coin):
    latest = df.iloc[-1]
    fib_level = is_near_fib_level(latest['price'], fib_levels)
    adx_value = f"{latest['adx']:.2f}" if not np.isnan(latest['adx']) else 'N/A'
    signal_vn = 'Mua' if latest['signal'] == 'Long' else 'Bán' if latest['signal'] == 'Short' else 'Giữ'
    
    output = (
        f"### Phân Tích {coin}\n"
        f"- **Tín hiệu**: {signal_vn}\n"
        f"- **Giá**: ${latest['price']:,.2f}\n"
        f"- **RSI**: {latest['rsi']:.1f}\n"
        f"- **MACD**: {latest['macd']:.0f}, Signal: {latest['macd_signal']:.0f}\n"
        f"- **BB**: ${latest['bb_high']:,.0f}/${latest['bb_low']:,.0f}\n"
        f"- **ADX**: {adx_value}\n"
        f"- **Fib**: {fib_level or 'N/A'}\n"
    )
    
    support, resistance = get_support_resistance(df, fib_levels)
    gemini_result = get_gemini_recommendation(latest, fib_level, support, resistance, coin)
    
    strategy_output = "\n### AI Strategy\n"
    strategy_output += f"{'Kịch bản':<15} | {'Xác nhận':<40} | {'Hành động':<15} | {'Mục tiêu':<15}\n"
    strategy_output += "-" * 85 + "\n"
    for strategy in gemini_result['strategy']:
        try:
            targets = ', '.join([f"${float(t):.0f}" for t in strategy['target']]) if strategy['target'] else 'N/A'
        except:
            targets = 'N/A'
        strategy_output += f"{strategy['scenario']:<15} | {strategy['confirmation']:<40} | {strategy['action']:<15} | {targets:<15}\n"
    strategy_output += "-" * 85
    
    logging.info(output + strategy_output)
    return output, strategy_output, gemini_result

# PandasDataExtended
class PandasDataExtended(bt.feeds.PandasData):
    lines = ('signal',)
    params = (('signal', -1),)

# Chiến lược backtest
class SignalStrategy(bt.Strategy):
    params = (('stop_loss', 0.05),)

    def __init__(self):
        self.signal = self.datas[0].signal
        self.position_size = 0
        self.entry_price = None

    def next(self):
        price = self.data.close[0]
        if self.position_size == 0:
            if self.signal[0] == 1:
                self.buy(size=1)
                self.position_size = 1
                self.entry_price = price
            elif self.signal[0] == -1:
                self.sell(size=1)
                self.position_size = -1
                self.entry_price = price
        elif self.position_size == 1:
            if price <= self.entry_price * (1 - self.params.stop_loss):
                self.sell(size=1)
                self.position_size = 0
            elif self.signal[0] != 1
