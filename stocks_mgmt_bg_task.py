import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
from datetime import datetime, time
import plotly.express as px
import os
import yfinance as yf
from decimal import Decimal, getcontext, ROUND_HALF_UP, InvalidOperation
import time as time_lib

import gspread
import toml
from zoneinfo import ZoneInfo
#Execute cmd: python futures_mgmt_bg_task.py

# --- 1. 工具函式 (Utils) ---

def to_decimal(val) -> Decimal:
    """安全地將數值轉為 Decimal，處理 NaN 或字串"""
    if isinstance(val, Decimal):
        return val
    try:
        if pd.isna(val):
            return Decimal('0')
        s = str(val).replace(',', '').strip()
        if not s or s.lower() == 'nan':
            return Decimal('0')
        return Decimal(s)
    except (ValueError, InvalidOperation):
        return Decimal('0')

def safe_div(a: Decimal, b: Decimal) -> Decimal:
    """安全除法，避免除以零"""
    if b == Decimal('0'):
        return Decimal('0')
    return (a / b).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

# --- 3. 核心邏輯類別 ---
class PortfolioManager:
    def __init__(self):
        """
        初始化：讀取 config、設定檔路徑，並建立 Google Sheets 連線
        """
        self.config = {}
        self.sh = None
        
        try:
            # 1. 讀取 secrets.toml
            with open(".streamlit/secrets.toml", "r") as f:
                self.config = toml.load(f)

            # 2. 獲取 gsheets 設定區塊
            gsheets_config = self.config["connections"]["gsheets"]
            self.spreadsheet_url = gsheets_config["spreadsheet"]
            
            # 3. 準備驗證用的字典 (將 spreadsheet 網址排除，只保留 GCP 憑證欄位)
            creds_dict = {k: v for k, v in gsheets_config.items() if k != "spreadsheet"}
            
            # 4. 直接使用字典進行身分驗證，不再需要獨立的 .json 檔案
            gc = gspread.service_account_from_dict(creds_dict)
            self.sh = gc.open_by_url(self.spreadsheet_url)
            
        except Exception as e:
            print(f"🔴 初始化連線或讀取 Config 失敗: {e}")
            
    # [修復]：移除 @staticmethod，因為需要用到 self.sh
    def load_data(self):
        """讀取 Google Sheets 資料"""
        try:
            if self.sh is None:
                raise Exception("尚未建立試算表連線")

            # 讀取 worksheet 0 (Portfolio)
            ws_portfolio = self.sh.get_worksheet(2)
            df_portfolio = pd.DataFrame(ws_portfolio.get_all_records())
            
            # 讀取 worksheet 1 (Asset)
            ws_asset = self.sh.get_worksheet(3)
            df_asset = pd.DataFrame(ws_asset.get_all_records())
            return df_portfolio, df_asset
        except Exception as e:
            print(f"🔴 無法讀取 Google Sheets，請檢查權限或網址。錯誤訊息: {e}")
            return pd.DataFrame(), pd.DataFrame()
    @staticmethod
    @st.cache_data(ttl=60, show_spinner=False)
    def fetch_current_price(ticker: str):
        """
        取得單一股票價格，快取 60 秒。
        優先順序: fast_info -> history(1d) -> 嘗試切換 .TW/.TWO
        """
        if not ticker: 
            return None
        
        # 1. 嘗試直接抓取
        price = PortfolioManager._get_yfinance_price(ticker)
        
        # 2. 若失敗，嘗試智能判斷後綴 (.TW / .TWO)
        if price is None:
            base_ticker = ticker.replace('.TW', '').replace('.TWO', '')
            # 先試 .TW
            price = PortfolioManager._get_yfinance_price(f"{base_ticker}.TW")
            # 再試 .TWO
            if price is None:
                price = PortfolioManager._get_yfinance_price(f"{base_ticker}.TWO")
        
        return price

    @staticmethod
    def _get_yfinance_price(ticker: str):
        """底層抓價邏輯"""
        try:
            t = yf.Ticker(ticker)
            # 方法 A: fast_info (通常最快)
            if hasattr(t, "fast_info"):
                price = t.fast_info.get("last_price")
                if price and not pd.isna(price):
                    return float(price)
            
            # 方法 B: history (較慢但穩)
            hist = t.history(period='1d')
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
        except Exception:
            pass
        return None

    @staticmethod
    def calculate_metrics(df_raw, fetch_live: bool = True):
        """
        計算所有財務指標
        :param df_raw: 原始 DataFrame
        :param fetch_live: 是否連網抓取即時報價
        """
        # 1. 提取現金 (第一列)
        asset_info = df_raw.iloc[0].to_dict()
        cash = to_decimal(asset_info.get('總現金', 0))

        # 2. 處理持倉 (從第二列開始)
        df = df_raw.iloc[1:].copy()
        
        # 自動產生代碼欄位 (若 CSV 只有名稱)
        if '代碼' not in df.columns:
            df['代碼'] = df['名稱'].astype(str).apply(
                lambda x: f"{x}.TW" if not (x.endswith('.TW') or x.endswith('.TWO')) else x
            )

        # 3. 取得報價
        current_prices = []
        has_live_data = False
        
        for _, row in df.iterrows():
            ticker = row['代碼']
            price = None
            if fetch_live:
                price = PortfolioManager.fetch_current_price(ticker)
                print(f"ticker: {ticker} price: {price}")
            # 若抓不到或不抓，使用 CSV 舊值
            if price is None:
                price = row.get('最新報價', 0)
            else:
                has_live_data = True
            
            current_prices.append(price)

        df['最新報價'] = current_prices

        # 4. 數值計算 (使用 Decimal 確保精度)
        df['平均成本_d'] = df['平均成本'].apply(to_decimal)
        df['股數_d'] = df['股數'].apply(to_decimal)
        df['最新報價_d'] = df['最新報價'].apply(to_decimal)

        df['當前市值_d'] = (df['最新報價_d'] * df['股數_d']).apply(lambda x: x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        df['總成本_d'] = (df['平均成本_d'] * df['股數_d']).apply(lambda x: x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        df['未實現損益_d'] = df['當前市值_d'] - df['總成本_d']
        
        # 總計
        total_stock_value = sum(df['當前市值_d'])
        total_assets = total_stock_value + cash
        total_unrealized_pl = sum(df['未實現損益_d'])
        
        # 計算個別權重與報酬率
        df['總占比'] = df['當前市值_d'].apply(lambda x: float(safe_div(x, total_assets)))
        
        def calc_roi(row):
            cost = row['總成本_d']
            if cost == 0: return 0.0
            return float(safe_div(row['未實現損益_d'], cost))
        
        df['報酬率'] = df.apply(calc_roi, axis=1)

        # 轉回 float 供顯示與繪圖
        result_df = df.copy()
        for col in ['當前市值', '總成本', '未實現損益']:
            result_df[col] = df[f'{col}_d'].apply(float)
            
        # 確保顯示欄位存在
        display_cols = ['公司名稱', '代碼', '族群', '股數', '平均成本', '最新報價', '當前市值', '總成本', '未實現損益', '報酬率', '總占比']
        for c in display_cols:
            if c not in result_df.columns: result_df[c] = 0
            
        return {
            'df': result_df[display_cols],
            'total_assets': float(total_assets),
            'total_cash': float(cash),
            'total_stock_value': float(total_stock_value),
            'total_unrealized_pl': float(total_unrealized_pl),
            'has_live_data': has_live_data,
            'updated_at': datetime.now(ZoneInfo("Asia/Taipei"))
        }

    # [修復]：移除 @staticmethod，因為需要用到 self.sh
    def save_data(self, original_df_raw, result_df, current_asset_value, df_asset_history):
        try:
            if self.sh is None:
                raise Exception("尚未建立試算表連線")
                
            # 1. 更新 Portfolio (回填最新報價)
            if '最新報價' not in original_df_raw.columns:
                original_df_raw['最新報價'] = 0.0
            
            original_df_raw.iloc[1:, original_df_raw.columns.get_loc('最新報價')] = result_df['最新報價'].values

            ws_portfolio = self.sh.get_worksheet(2)
            ws_portfolio.clear()
            # ws_portfolio.update(range_name="A1", value=[original_df_raw.columns.tolist()] + original_df_raw.values.tolist())
            ws_portfolio.update([original_df_raw.columns.tolist()] + original_df_raw.values.tolist())
            # 2. 更新 Asset History
            today_str = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")
            
            if today_str in df_asset_history['日期'].values:
                df_asset_history.loc[df_asset_history['日期'] == today_str, '總價值'] = current_asset_value
                msg = f"已更新今日 ({today_str}) 資產紀錄"
            else:
                new_row = pd.DataFrame({'日期': [today_str], '總價值': [current_asset_value]})
                df_asset_history = pd.concat([df_asset_history, new_row], ignore_index=True)
                msg = f"已新增今日 ({today_str}) 資產紀錄"

            df_asset_history.sort_values('日期', inplace=True)

            ws_asset = self.sh.get_worksheet(3)
            ws_asset.clear()
            # ws_asset.update(range_name="A1", value=[df_asset_history.columns.tolist()] + df_asset_history.values.tolist())
            ws_asset.update([df_asset_history.columns.tolist()] + df_asset_history.values.tolist())
            return True, msg
        except Exception as e:
            return False, f"儲存失敗: {e}"


# --- 4. Streamlit UI ---
def main():
    manager = PortfolioManager()
    print(f"Stock背景監控進程已啟動: {datetime.now(ZoneInfo("Asia/Taipei"))}")

    while True:
        try:
            current_time = datetime.now(ZoneInfo("Asia/Taipei")).time()
            # 定義時段門檻
            day_start = time(8, 45)
            day_end = time(13, 45)
            night_start = time(15, 0)
            night_end = time(5, 0)

            # 判斷邏輯
            is_day_session = day_start <= current_time <= day_end
            # 夜盤跨日判斷：大於等於 15:00 OR 小於等於 05:00
            is_night_session = current_time >= night_start or current_time <= night_end

            if is_day_session:
                sleeptime = 300 # 5m
            elif is_night_session:
                sleeptime = 3600 # 1hr
            else:
                time_lib.sleep(60)
                continue

            # 1. 執行核心任務：載入資料並抓取最新報價
            df_raw, df_asset_hist = manager.load_data()
            if not df_raw.empty:
                data = manager.calculate_metrics(df_raw, fetch_live=True)
                print(f"執行自動存檔...")
                manager.save_data(df_raw, data['df'], data['total_assets'], df_asset_hist)

            print(f"✅ 循環檢查完成: {current_time.strftime('%H:%M:%S')}")
            time_lib.sleep(sleeptime) 

        except Exception as e:
            print(f"❌ 背景迴圈發生異常: {e}")
            time_lib.sleep(60)

if __name__ == "__main__":
    main()