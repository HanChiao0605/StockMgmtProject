import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
from datetime import datetime, time
import plotly.express as px
import os
import random
import time as time_lib

# --- Selenium 相關引用 ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

import gspread
import toml
from zoneinfo import ZoneInfo
#Execute cmd: python future_mgmt_task.py
# --- 1. 全域設定 ---
EQITY_THRESHOLD = 6000
ASSET_UNIT = 10000 # 10k per unit

FUTURES_URLS = {
    '大台': 'https://www.wantgoo.com/futures/wtx&',
    '小台': 'https://www.wantgoo.com/futures/wmt&',
    '微台': 'https://www.wantgoo.com/futures/wtmp&'
}

# --- 保證金設定 (包含原始與維持) ---
MARGIN_CONFIG = {
    '大台': {'Initial': 374000, 'Maintenance': 287000},
    '小台': {'Initial': 93500,  'Maintenance': 71750},
    '微台': {'Initial': 18700,  'Maintenance': 14350}
}

# --- 2. 爬蟲函數 ---
def get_futures_price_selenium(driver, url):
    """
    使用給定的 Selenium driver 爬取指定網址的期貨即時報價。
    這個方法能處理 JavaScript 動態載入的網頁內容。
    """
    try:
        driver.get(url)
        wait = WebDriverWait(driver, 20)
        price_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'deal')]")))
        for _ in range(30): 
            price_text = price_element.text.strip().replace(',', '')
            if price_text and price_text != '---': 
                break
            time_lib.sleep(1)
        else:   
            raise Exception("等待超時！無法在指定時間內獲取價格數據。")
            
        target_price = float(price_text)
        return target_price
        
    except Exception as e:
        print(f"抓取 {url} 時發生錯誤: {e}")
        # --- 除錯資訊收集區 ---
        try:
            # 1. 基本資訊：確認網址有沒有被重新導向，以及網頁的標題是什麼
            print(f"[Debug] 當前標題: {driver.title}")
            print(f"[Debug] 當前網址: {driver.current_url}")
            
            # 2. 無頭模式除錯神器：拍下發生錯誤那一瞬間的螢幕截圖
            # 這會把圖片存在你的專案資料夾中
            driver.save_screenshot(f"debug_timeout_{url.split('/')[-1]}.png")
            print(f"[Debug] 📸 已儲存畫面截圖至 debug_timeout_{url.split('/')[-1]}.png")
            
            # 3. 儲存完整的 DOM 原始碼 (包含 JS 渲染後的結果)
            # 因為直接 print 出來會大洗版，建議存成 html 檔案來分析
            with open(f"debug_source_{url.split('/')[-1]}.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print(f"[Debug] 📄 已儲存網頁原始碼至 debug_source_{url.split('/')[-1]}.html")
            
        except Exception as debug_e:
            print(f"[Debug] 嘗試收集除錯資訊時發生錯誤: {debug_e}")
            
        return None

# --- 3. 核心邏輯類別 ---
class FuturesManager:
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
            ws_portfolio = self.sh.get_worksheet(0)
            df_portfolio = pd.DataFrame(ws_portfolio.get_all_records())
            
            # 讀取 worksheet 1 (Asset)
            ws_asset = self.sh.get_worksheet(1)
            df_asset = pd.DataFrame(ws_asset.get_all_records())
            return df_portfolio, df_asset
        except Exception as e:
            print(f"🔴 無法讀取 Google Sheets，請檢查權限或網址。錯誤訊息: {e}")
            return pd.DataFrame(), pd.DataFrame()

    @staticmethod
    def _generate_ticker_code(row):
        """根據 CSV 內容動態生成期貨代碼 (WTXG4 等)"""
        ticker_map = {'大台': 'WTX', '小台': 'WMT', '微台': 'WTM'}
        month_map = {
            1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
            7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'
        }
        month_code = month_map.get(row['月份'], '')
        year_code = str(row['年份'])[3] # 取年份最後一碼
        return f"{ticker_map.get(row['名稱'], '')}{month_code}{year_code}"

    @staticmethod
    def fetch_live_prices(df_portfolio):
        """啟動 Selenium 並抓取報價"""
        live_prices = {}
        has_live_data = False
        driver = None

        temp_df = df_portfolio.iloc[1:].copy()
        temp_df['代碼'] = temp_df.apply(FuturesManager._generate_ticker_code, axis=1)
        unique_tickers = temp_df['代碼'].unique()

        # 定義 User-Agent 列表
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36",
        ]

        options = webdriver.ChromeOptions()

        options.add_argument(f"user-agent={random.choice(user_agents)}")
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--allow-insecure-localhost')
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--enable-unsafe-swiftshader')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        try:
            #for local
            service = ChromeService(ChromeDriverManager().install())
            #for streamlit remote
            # service = ChromeService("/usr/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            })
            for ticker in unique_tickers:
                url = f'https://www.wantgoo.com/futures/{str(ticker).lower()}'
                price = get_futures_price_selenium(driver, url)
                print(f"ticker: {ticker} price: {price}")
                if price is not None:
                    live_prices[ticker] = price
                    has_live_data = True
            
        except Exception as e:
            print(f"Selenium 啟動或抓取失敗: {e}")
        finally:
            if driver:
                driver.quit()
        
        return live_prices, has_live_data

    @staticmethod
    def calculate_metrics(df_raw, fetch_live):
        asset_status = df_raw.iloc[0].to_dict()
        account_remain = float(asset_status.get('帳戶現金餘額', 0))
        total_cash = float(asset_status.get('總現金', 0))

        df = df_raw.iloc[1:].copy()
        df['代碼'] = df.apply(FuturesManager._generate_ticker_code, axis=1)

        has_live_data = False
        if fetch_live:
            with st.spinner('正在啟動爬蟲抓取報價 (Selenium)...'):
                live_prices, has_live_data = FuturesManager.fetch_live_prices(df_raw)
            if has_live_data:
                df['最新報價'] = df['代碼'].map(live_prices).fillna(df['最新報價'])

        point_value_map = {'大台': 200, '小台': 50, '微台': 10}
        df['權重'] = df['名稱'].map(point_value_map)
        
        cols_to_numeric = ['最新報價', '平均成本', '口數', '權重']
        for col in cols_to_numeric:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        df['總點數'] = df['權重'] * df['口數']
        df['合約市值'] = df['最新報價'] * df['權重'] * df['口數']
        df['總成本'] = df['平均成本'] * df['權重'] * df['口數']
        df['未實現損益'] = df['合約市值'] - df['總成本']
        
        df['單口原始保證金'] = df['名稱'].map(lambda x: MARGIN_CONFIG.get(x, {}).get('Initial', 0)).fillna(0)
        df['單口維持保證金'] = df['名稱'].map(lambda x: MARGIN_CONFIG.get(x, {}).get('Maintenance', 0)).fillna(0)
        
        df['總原始保證金'] = df['單口原始保證金'] * df['口數']
        df['總維持保證金'] = df['單口維持保證金'] * df['口數']
        
        total_margin_point = df['總點數'].sum()
        total_margin_locked = df['總原始保證金'].sum()
        total_maintenance_margin = df['總維持保證金'].sum()
        total_unrealized_pl = df['未實現損益'].sum()

        current_equity = account_remain + total_unrealized_pl
        total_assets = current_equity + total_cash

        futures_total_market_value = df['合約市值'].sum()
        leverage_ratio = futures_total_market_value / total_assets if total_assets > 0 else 0
        usage_ratio = (total_margin_locked / current_equity) * 100 if current_equity > 0 else 0

        if total_maintenance_margin > 0:
            maintenance_ratio = (current_equity / total_maintenance_margin) * 100
            maintenance_point = (current_equity - total_maintenance_margin) / total_margin_point
        else:
            maintenance_ratio = 9999.0
            maintenance_point = 9999.0

        target_leverage = [2, 1.5, 1, 0.5]
        analysis_data = []
        
        micro_price = 0
        micro_row = df[df['名稱'] == '微台']
        if not micro_row.empty:
            micro_price = micro_row['最新報價'].iloc[0]

        if micro_price > 0:
            for target in target_leverage:
                target_val = target * total_assets
                val_to_add = target_val - futures_total_market_value
                lots_to_add = val_to_add / (micro_price * 10)
                analysis_data.append({
                    '目標槓桿': f"{target}x",
                    '目標合約總值': target_val,
                    '需增加市值': val_to_add,
                    '建議微台口數': lots_to_add
                })
        
        analysis_df = pd.DataFrame(analysis_data)
        return {
            'df': df,
            'analysis_df': analysis_df,
            'futures_total_value': futures_total_market_value,
            'current_equity': current_equity,
            'total_cash': total_cash,
            'total_margin_locked': total_margin_locked,
            'total_maintenance_margin': total_maintenance_margin,
            'usage_ratio': usage_ratio,
            'maintenance_ratio': maintenance_ratio,
            'maintenance_point':maintenance_point,
            'total_assets': total_assets,
            'leverage_ratio': leverage_ratio,
            'total_unrealized_pl': total_unrealized_pl,
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

            ws_portfolio = self.sh.get_worksheet(0)
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

            ws_asset = self.sh.get_worksheet(1)
            ws_asset.clear()
            # ws_asset.update(range_name="A1", value=[df_asset_history.columns.tolist()] + df_asset_history.values.tolist())
            ws_asset.update([df_asset_history.columns.tolist()] + df_asset_history.values.tolist())
            return True, msg
        except Exception as e:
            return False, f"儲存失敗: {e}"


# --- 4. Streamlit UI ---
def main():
    manager = FuturesManager()
    print(f"Future背景監控進程已啟動: {datetime.now(ZoneInfo("Asia/Taipei"))}")

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