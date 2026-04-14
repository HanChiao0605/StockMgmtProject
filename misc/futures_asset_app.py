import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
from datetime import datetime, time
import plotly.express as px
import os
import random
import time as time_lib

# --- Selenium 相關引用 (保持不變) ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from streamlit_gsheets import GSheetsConnection
from zoneinfo import ZoneInfo
#python -m streamlit run futures_asset_app.py
# --- 1. 全域設定 ---
st.set_page_config(page_title="期貨資產報告", layout="wide", page_icon="🤖")

FUTURES_PORTFOLIO_CSV_FILENAME = 'futures_portfolio.csv'
FUTURES_ASSET_CSV_FILENAME = 'futures_asset_value.csv'
EQITY_THRESHOLD = 6000
ASSET_UNIT = 10000 #10k per unit
# 定義每個期貨對應的 URL (保持不變)
FUTURES_URLS = {
    '大台': 'https://www.wantgoo.com/futures/wtx&',
    '小台': 'https://www.wantgoo.com/futures/wmt&',
    '微台': 'https://www.wantgoo.com/futures/wtmp&'
}

# --- [修改] 保證金設定 (包含原始與維持) ---
# 結構: '名稱': {'Initial': 原始保證金, 'Maintenance': 維持保證金}
# 數值僅供參考，請依期交所最新公告更新
MARGIN_CONFIG = {
    '大台': {'Initial': 374000, 'Maintenance': 287000},
    '小台': {'Initial': 93500,  'Maintenance': 71750},
    '微台': {'Initial': 18700,  'Maintenance': 14350}
}

# --- 2. 爬蟲函數 (完全不做更改) ---
def get_futures_price_selenium(driver, url):
    """
    使用給定的 Selenium driver 爬取指定網址的期貨即時報價。
    這個方法能處理 JavaScript 動態載入的網頁內容。
    """
    try:
        # 導航到目標網站
        driver.get(url)
        
        # 顯式等待，直到價格元素被載入
        wait = WebDriverWait(driver, 20)
        # 更新為更穩定的 XPath，以應對網站結構變動
        price_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'deal')]")))

        # 新增一個迴圈，等待直到價格內容載入
        for _ in range(10): # 最多等待10秒
            price_text = price_element.text.strip().replace(',', '')
            if price_text and price_text != '---': # 檢查價格是否為有效數字
                break
            time_lib.sleep(1) # 暫停1秒後再檢查
        else:
            raise Exception("等待超時！無法在指定時間內獲取價格數據。")
            
        target_price = float(price_text)
        
        return target_price
        
    except Exception as e:
        print(f"抓取 {url} 時發生錯誤: {e}")
        return None

# --- 3. 核心邏輯類別 ---

class FuturesManager:
    @staticmethod
    def load_data():
        try:
            # .streamlit/secrets.toml
            # st.write(st.secrets)
            conn = st.connection("gsheets", type=GSheetsConnection)
            # st.write(conn.read(worksheet=0, ttl=0))
            # st.write(conn.read(worksheet=1, ttl=0))
            df_portfolio = conn.read(worksheet=0, ttl=0)
            df_asset = conn.read(worksheet=1, ttl=0)
            return df_portfolio, df_asset
        except Exception as e:
            st.error(f"無法讀取 Google Sheets，請檢查權限或網址。錯誤訊息: {e}")
        return pd.DataFrame() # 回傳空表避免後面程式碼報錯
        # if not os.path.exists(FUTURES_PORTFOLIO_CSV_FILENAME):
        #     st.error(f"找不到 '{FUTURES_PORTFOLIO_CSV_FILENAME}'。")
        #     return None, None
        # try:
        #     df_portfolio = pd.read_csv(FUTURES_PORTFOLIO_CSV_FILENAME)
        #     if os.path.exists(FUTURES_ASSET_CSV_FILENAME):
        #         df_asset = pd.read_csv(FUTURES_ASSET_CSV_FILENAME)
        #     else:
        #         df_asset = pd.DataFrame(columns=['日期', '總價值'])
        #     return df_portfolio, df_asset
        # except Exception as e:
        #     st.error(f"讀取 CSV 錯誤: {e}")
        #     return None, None

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

        # 產生代碼與 URL 對應
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
            #for local test
            service = ChromeService(ChromeDriverManager().install())
            #for streamlit remote
            # service = ChromeService("/usr/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            })

            # 遍歷抓取
            for ticker in unique_tickers:
                url = f'https://www.wantgoo.com/futures/{str(ticker).lower()}'
                print(url)
                price = get_futures_price_selenium(driver, url)
                if price is not None:
                    live_prices[ticker] = price
                    has_live_data = True
            
        except Exception as e:
            st.error(f"Selenium 啟動或抓取失敗: {e}")
        finally:
            if driver:
                driver.quit()
        
        return live_prices, has_live_data

    @staticmethod
    def calculate_metrics(df_raw, fetch_live=True):
        # 1. 提取帳戶資訊
        asset_status = df_raw.iloc[0].to_dict()
        account_remain = float(asset_status.get('帳戶現金餘額', 0))
        total_cash = float(asset_status.get('總現金', 0))

        # 2. 處理持倉
        df = df_raw.iloc[1:].copy()
        df['代碼'] = df.apply(FuturesManager._generate_ticker_code, axis=1)

        # 3. 取得報價
        has_live_data = False
        if fetch_live:
            with st.spinner('正在啟動爬蟲抓取報價 (Selenium)...'):
                live_prices, has_live_data = FuturesManager.fetch_live_prices(df_raw)
            if has_live_data:
                df['最新報價'] = df['代碼'].map(live_prices).fillna(df['最新報價'])

        # 4. 基礎計算 (點值)
        point_value_map = {'大台': 200, '小台': 50, '微台': 10}
        df['權重'] = df['名稱'].map(point_value_map)
        
        cols_to_numeric = ['最新報價', '平均成本', '口數', '權重']
        for col in cols_to_numeric:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 總點數 = 權重(點值) * 口數
        df['總點數'] = df['權重'] * df['口數']
        df['合約市值'] = df['最新報價'] * df['權重'] * df['口數']
        df['總成本'] = df['平均成本'] * df['權重'] * df['口數']
        df['未實現損益'] = df['合約市值'] - df['總成本']
        # 取得 原始保證金 & 維持保證金
        df['單口原始保證金'] = df['名稱'].map(lambda x: MARGIN_CONFIG.get(x, {}).get('Initial', 0)).fillna(0)
        df['單口維持保證金'] = df['名稱'].map(lambda x: MARGIN_CONFIG.get(x, {}).get('Maintenance', 0)).fillna(0)
        
        df['總原始保證金'] = df['單口原始保證金'] * df['口數']
        df['總維持保證金'] = df['單口維持保證金'] * df['口數']
        
        total_margin_point = df['總點數'].sum()
        total_margin_locked = df['總原始保證金'].sum()   # 已佔用原始保證金
        total_maintenance_margin = df['總維持保證金'].sum() # 帳戶所需維持保證金
        total_unrealized_pl = df['未實現損益'].sum() # 浮動損益

        # 權益數計算: 帳戶餘額 + 浮動損益
        current_equity = account_remain + total_unrealized_pl
        
        total_assets = current_equity + total_cash

        # 槓桿計算
        futures_total_market_value = df['合約市值'].sum()
        leverage_ratio = futures_total_market_value / total_assets if total_assets > 0 else 0
        
        # 資金使用率
        usage_ratio = (total_margin_locked / current_equity) * 100 if current_equity > 0 else 0

        # --- [新增] 維持率計算 ---
        # 公式: 權益數 / 總維持保證金
        if total_maintenance_margin > 0:
            maintenance_ratio = (current_equity / total_maintenance_margin) * 100
            maintenance_point = (current_equity - total_maintenance_margin) / total_margin_point
        else:
            maintenance_ratio = 9999.0 # 無部位時顯示安全值
            maintenance_point = 9999.0 # 無部位時顯示安全值

        # 5. 槓桿建議計算 (微台)
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

    @staticmethod
    def save_data(original_df_raw, result_df, current_asset_value, df_asset_history):
        try:
            # .streamlit/secrets.toml
            conn = st.connection("gsheets", type=GSheetsConnection)
            # 1. 更新 Portfolio (回填最新報價)
            if '最新報價' not in original_df_raw.columns:
                original_df_raw['最新報價'] = 0.0
            
            original_df_raw.iloc[1:, original_df_raw.columns.get_loc('最新報價')] = result_df['最新報價'].values
            conn.update(worksheet=0, data=original_df_raw)

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
            conn.update(worksheet=1, data=df_asset_history)
            return True, msg
        except Exception as e:
            return False, f"儲存失敗: {e}"

# --- 4. Streamlit UI ---

def main():
    # --- 側邊欄 ---
    with st.sidebar:
        st.header("⚙️ 設定")
        enable_autorefresh = st.checkbox("啟用自動刷新 (30分鐘)", value=True)
        auto_save_time = st.time_input("自動存檔時間 (收盤後)", value=time(13, 45))
        st.divider()
        force_save = st.button("💾 立即強制存檔", type="primary")

        st.markdown("---")
        st.markdown("ℹ️ **保證金參數 (原始/維持):**")
        for k, v in MARGIN_CONFIG.items():
            st.caption(f"- {k}: ${v['Initial']:,} / ${v['Maintenance']:,}")

        if enable_autorefresh:
            st_autorefresh(interval=1800000, key="futures_refresh")

    st.title("期貨資產")
    
    # 載入資料
    df_raw, df_asset_hist = FuturesManager.load_data()
    if df_raw is None: st.stop()

    # 計算
    data = FuturesManager.calculate_metrics(df_raw, fetch_live=True)
    summary_df = data['df']

    # --- 自動存檔 ---
    try:
        current_dt = datetime.now(ZoneInfo("Asia/Taipei"))
        current_date_str = current_dt.strftime("%Y/%m/%d")
        
        is_recorded_today = False
        if df_asset_hist is not None and not df_asset_hist.empty:
            is_recorded_today = current_date_str in df_asset_hist['日期'].values

        if (enable_autorefresh and 
            current_dt.time() > auto_save_time and 
            not is_recorded_today and 
            data['has_live_data']):
            
            st.toast("⏳ 執行收盤自動存檔...", icon="🤖")
            success, msg = FuturesManager.save_data(df_raw, summary_df, data['total_assets'], df_asset_hist)
            if success:
                st.toast(f"✅ 自動存檔完成: {msg}", icon="💾")
                _, df_asset_hist = FuturesManager.load_data()
    except Exception as e:
        st.warning(f"自動存檔錯誤: {e}")

    # --- 手動存檔 ---
    if force_save:
        if data['has_live_data']:
            success, msg = FuturesManager.save_data(df_raw, summary_df, data['total_assets'], df_asset_hist)
            if success:
                st.success(msg)
                _, df_asset_hist = FuturesManager.load_data()
            else:
                st.error(msg)
        else:
            st.warning("⚠️ 無即時報價，取消存檔。")

    # --- Metrics 顯示 ---
    # 改為 8 欄位以容納維持率
    cols = st.columns(8)
    
    cols[0].metric("期貨部位", f"${(data['futures_total_value']/ASSET_UNIT):,.1f}W")
    cols[1].metric("總資產", f"${(data['total_assets']/ASSET_UNIT):,.1f}W")
    cols[2].metric("權益總值", f"${(data['current_equity']/ASSET_UNIT):,.1f}W", help="權益數 = 帳戶現金餘額 + 未實現損益")
    cols[3].metric("活存現金", f"${(data['total_cash']/ASSET_UNIT):,.1f}W")
    #cols[2].metric("已佔用保證金", f"${data['total_margin_locked']:,.0f}")
    
    cols[4].metric("未實現損益", f"${(data['total_unrealized_pl']/ASSET_UNIT):,.1f}W", 
                delta=f"{(data['total_unrealized_pl']/ASSET_UNIT):,.1f}", delta_color="inverse")
    
    cols[5].metric("槓桿倍率", f"{data['leverage_ratio']:.2f}x")
    
    # 資金使用率
    #usage_val = data['usage_ratio']
    # cols[4].metric("資金使用率", f"{usage_val:.1f}%", 
    #                delta="注意" if usage_val > 70 else "安全", delta_color="inverse")

    # 維持率
    m_ratio = data['maintenance_ratio']
    m_ratio_str = f"{m_ratio:.1f}%"

    # 維持率顏色邏輯: <100% 危險(紅), <130% 警告(紅), >130% 安全
    delta_color = "normal" if m_ratio > 130 else "inverse" # inverse 在 delta 是紅色
    delta_msg = "安全"
    if m_ratio < 100: delta_msg = "❌ 追繳/平倉"
    elif m_ratio < 130: delta_msg = "⚠️ 注意"
    
    cols[6].metric("維持率", m_ratio_str, delta=delta_msg, delta_color=delta_color)

    # 維持點數
    m_point = data['maintenance_point']
    m_point_str = f"{m_point:.1f}"
       # 維持率顏色邏輯: <100% 危險(紅), <130% 警告(紅), >130% 安全
    delta_color = "normal" if m_point > EQITY_THRESHOLD else "inverse" # inverse 在 delta 是紅色
    delta_msg = "安全"
    if m_point < (EQITY_THRESHOLD/2): delta_msg = "❌ 危險"
    elif m_point < EQITY_THRESHOLD: delta_msg = "⚠️ 注意"

    cols[7].metric("維持點數", m_point_str, delta=delta_msg, delta_color=delta_color)

    st.caption(f"最後更新: {data['updated_at'].strftime('%Y-%m-%d %H:%M:%S')} "
               f"{'(🟢 即時)' if data['has_live_data'] else '(🔴 舊資料)'}")

    # --- Tabs ---
    tab1, tab2, tab3 = st.tabs(["📋 部位 & 保證金", "🧮 槓桿分析", "📈 期貨總價值走勢"])

    def style_futures(val):
        if val > 0: return 'color: #ff4b4b; font-weight: bold'
        elif val < 0: return 'color: #09ab3b; font-weight: bold'
        return ''

    with tab1:
        # 顯示欄位包含維持保證金
        display_df = summary_df[['名稱', '代碼', '口數', '平均成本', '最新報價', '總原始保證金', '總維持保證金', '未實現損益']].copy()
        st.dataframe(display_df.style.format({
            '平均成本': '{:,.0f}',
            '最新報價': '{:,.0f}',
            '總原始保證金': '{:,.0f}',
            '總維持保證金': '{:,.0f}',
            '未實現損益': '{:,.0f}'
        }).map(style_futures, subset=['未實現損益']), width='stretch')
        
        st.info("💡 **維持率公式**：權益總值 / 總維持保證金。若低於 100% 需補錢，低於 25% 會被強制平倉。")

    with tab2:
        st.markdown("##### 🎯 槓桿調整建議 (基於當前權益數)")
        
        analysis_df = data['analysis_df']
        if not analysis_df.empty:
            st.dataframe(analysis_df.style.format({
                '目標合約總值': '${:,.0f}',
                '需增加市值': '${:,.0f}',
                '建議微台口數': '{:+.2f} 口'
            }).map(lambda x: 'color: #ff4b4b' if x > 0 else 'color: #09ab3b', subset=['建議微台口數']), 
            width='stretch')
        else:
            st.warning("無法計算建議 (可能缺少微台報價)。")

    with tab3:
        if df_asset_hist is not None and not df_asset_hist.empty:
            fig = px.line(df_asset_hist, x='日期', y='總價值', title='期貨部位總資產趨勢圖', markers=True)
            fig.update_traces(line_color='#1f77b4')
            fig.update_layout(hovermode="x unified")
            st.plotly_chart(fig, width='stretch')
            
            with st.expander("查看原始數據"):
                st.dataframe(df_asset_hist.sort_values('日期', ascending=False), width='stretch')

if __name__ == "__main__":
    main()