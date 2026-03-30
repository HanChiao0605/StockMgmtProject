import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
from datetime import datetime, time
import yfinance as yf
from decimal import Decimal, getcontext, ROUND_HALF_UP, InvalidOperation
import plotly.express as px
import os
# 執行指令: python -m streamlit run stocks_asset_app_US.py
# --- 1. 全域設定 ---
getcontext().prec = 28
# 設定檔案名稱 (美股版)
STOCKS_PORTFOLIO_CSV = 'stocks_portfolio_US.csv'
STOCKS_ASSET_CSV = 'stocks_asset_value_US.csv'

st.set_page_config(
    page_title="美股持倉報告 (US Stocks)", 
    layout="wide", 
    page_icon="🇺🇸"
)

# --- 2. 工具函式 (Utils) ---

def to_decimal(val) -> Decimal:
    """安全地將數值轉為 Decimal"""
    if isinstance(val, Decimal): return val
    try:
        if pd.isna(val): return Decimal('0')
        s = str(val).replace(',', '').replace('$', '').strip()
        if not s or s.lower() == 'nan': return Decimal('0')
        return Decimal(s)
    except (ValueError, InvalidOperation): return Decimal('0')

def safe_div(a: Decimal, b: Decimal) -> Decimal:
    """安全除法"""
    if b == Decimal('0'): return Decimal('0')
    return (a / b).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

# --- 3. 核心邏輯類別 (Core Logic) ---

class PortfolioManager:
    @staticmethod
    def load_data():
        if not os.path.exists(STOCKS_PORTFOLIO_CSV):
            st.error(f"找不到 '{STOCKS_PORTFOLIO_CSV}'，請確認檔案位置。")
            return None, None
        try:
            df_portfolio = pd.read_csv(STOCKS_PORTFOLIO_CSV)
            if os.path.exists(STOCKS_ASSET_CSV):
                df_asset = pd.read_csv(STOCKS_ASSET_CSV)
            else:
                df_asset = pd.DataFrame(columns=['日期', '總價值'])
            return df_portfolio, df_asset
        except Exception as e:
            st.error(f"讀取 CSV 錯誤: {e}")
            return None, None

    @staticmethod
    @st.cache_data(ttl=60, show_spinner=False)
    def fetch_current_price(ticker: str):
        """取得美股價格"""
        if not ticker: return None
        try:
            t = yf.Ticker(ticker)
            # 方法 A: fast_info
            if hasattr(t, "fast_info"):
                price = t.fast_info.get("last_price")
                if price and not pd.isna(price): return float(price)
            # 方法 B: history
            hist = t.history(period='1d')
            if not hist.empty: return float(hist['Close'].iloc[-1])
        except Exception: 
            pass
        return None

    @staticmethod
    def calculate_metrics(df_raw, fetch_live: bool = True):
        # 1. 提取現金
        asset_info = df_raw.iloc[0].to_dict()
        cash = to_decimal(asset_info.get('總現金', 0))
        
        # 2. 處理持倉
        df = df_raw.iloc[1:].copy()
        
        # 美股通常 CSV 的「名稱」就是代碼 (例如 AAPL)，若無代碼欄位則直接使用名稱
        if '代碼' not in df.columns:
            df['代碼'] = df['名稱'].astype(str).str.strip()
        
        # 確保有 '公司名稱' 欄位，若無則用代碼代替
        if '公司名稱' not in df.columns:
            df['公司名稱'] = df['代碼']

        # 3. 取得報價
        current_prices = []
        has_live_data = False
        
        for _, row in df.iterrows():
            ticker = row['代碼']
            price = None
            if fetch_live:
                price = PortfolioManager.fetch_current_price(ticker)
            
            if price is None:
                price = row.get('最新報價', 0)
            else:
                has_live_data = True
            current_prices.append(price)

        df['最新報價'] = current_prices

        # 4. 數值計算
        df['平均成本_d'] = df['平均成本'].apply(to_decimal)
        df['股數_d'] = df['股數'].apply(to_decimal)
        df['最新報價_d'] = df['最新報價'].apply(to_decimal)

        df['當前市值_d'] = (df['最新報價_d'] * df['股數_d']).apply(lambda x: x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        df['總成本_d'] = (df['平均成本_d'] * df['股數_d']).apply(lambda x: x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        df['未實現損益_d'] = df['當前市值_d'] - df['總成本_d']
        
        total_stock_value = sum(df['當前市值_d'])
        total_assets = total_stock_value + cash
        total_unrealized_pl = sum(df['未實現損益_d'])
        
        df['總占比'] = df['當前市值_d'].apply(lambda x: float(safe_div(x, total_assets)))
        
        def calc_roi(row):
            cost = row['總成本_d']
            if cost == 0: return 0.0
            return float(safe_div(row['未實現損益_d'], cost))
        
        df['報酬率'] = df.apply(calc_roi, axis=1)

        result_df = df.copy()
        for col in ['當前市值', '總成本', '未實現損益']:
            result_df[col] = df[f'{col}_d'].apply(float)
            
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
            'updated_at': datetime.now()
        }

    @staticmethod
    def save_data(original_df_raw, result_df, current_total_assets, df_asset_history):
        try:
            # 1. 更新 Portfolio
            if '最新報價' not in original_df_raw.columns:
                original_df_raw['最新報價'] = 0.0
            original_df_raw.loc[1:, '最新報價'] = result_df['最新報價'].values
            original_df_raw.to_csv(STOCKS_PORTFOLIO_CSV, index=False, encoding='utf-8-sig')

            # 2. 更新 Asset History
            today_str = datetime.now().strftime("%Y/%m/%d")
            if today_str in df_asset_history['日期'].values:
                df_asset_history.loc[df_asset_history['日期'] == today_str, '總價值'] = current_total_assets
                msg = f"已更新今日 ({today_str}) 資產紀錄"
            else:
                new_row = pd.DataFrame({'日期': [today_str], '總價值': [current_total_assets]})
                df_asset_history = pd.concat([df_asset_history, new_row], ignore_index=True)
                msg = f"已新增今日 ({today_str}) 資產紀錄"
            
            df_asset_history.sort_values('日期', inplace=True)
            df_asset_history.to_csv(STOCKS_ASSET_CSV, index=False, encoding='utf-8-sig')
            return True, msg
        except Exception as e:
            return False, f"儲存失敗: {e}"

# --- 4. Streamlit 主程式 ---

def main():
    with st.sidebar:
        st.header("⚙️ 美股設定")
        enable_autorefresh = st.checkbox("啟用自動刷新 & 自動存檔", value=True)
        # 美股收盤通常是台灣凌晨，預設設為早上 05:05
        auto_save_trigger_time = st.time_input("每日自動存檔時間 (收盤後)", value=time(5, 5))
        st.divider()
        force_save = st.button("💾 立即強制存檔", type="primary")
        
        st.info(f"當時間超過 **{auto_save_trigger_time.strftime('%H:%M')}** 且今日未記錄時，系統將自動寫入 CSV。")

        if enable_autorefresh:
            st_autorefresh(interval=300 * 1000, key="us_stock_refresh")

    st.title("🇺🇸 美股持倉報告")
    
    df_raw, df_asset_hist = PortfolioManager.load_data()
    if df_raw is None: st.stop()

    with st.spinner('正在同步美股報價...'):
        data = PortfolioManager.calculate_metrics(df_raw, fetch_live=True)
    
    summary_df = data['df']

    # --- 自動存檔邏輯 ---
    try:
        current_dt = datetime.now()
        current_date_str = current_dt.strftime("%Y/%m/%d")
        is_recorded_today = False
        if df_asset_hist is not None and not df_asset_hist.empty:
            is_recorded_today = current_date_str in df_asset_hist['日期'].values

        if (enable_autorefresh and 
            current_dt.time() > auto_save_trigger_time and 
            not is_recorded_today and 
            data['has_live_data']):
            
            st.toast(f"⏳ (美股) 收盤時間已過，正在執行自動記帳...", icon="🤖")
            success, msg = PortfolioManager.save_data(df_raw, summary_df, data['total_assets'], df_asset_hist)
            if success:
                st.toast(f"✅ 自動記帳完成：{msg}", icon="💾")
                _, df_asset_hist = PortfolioManager.load_data()
            else:
                st.error(f"❌ 自動記帳失敗：{msg}")
    except Exception as e:
        st.warning(f"自動存檔檢查發生錯誤: {e}")

    # --- 手動存檔邏輯 ---
    if force_save:
        if data['has_live_data']:
            success, msg = PortfolioManager.save_data(df_raw, summary_df, data['total_assets'], df_asset_hist)
            if success:
                st.toast(msg, icon='✅')
                st.success(msg)
                _, df_asset_hist = PortfolioManager.load_data()
            else:
                st.error(msg)
        else:
            st.warning("⚠️ 無法取得即時報價，為保護資料正確性，取消存檔。")

    # --- 顯示指標 ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("總資產 (USD)", f"${data['total_assets']:,.0f}")
    col2.metric("現金部位 (USD)", f"${data['total_cash']:,.0f}")
    col3.metric("股票市值 (USD)", f"${data['total_stock_value']:,.0f}")
    pl_val = data['total_unrealized_pl']
    
    # 美股顏色設定：normal (綠漲紅跌)
    col4.metric(
        "未實現損益 (USD)", 
        f"${pl_val:,.0f}", 
        delta=f"{pl_val/data['total_stock_value']:.2%}" if data['total_stock_value'] != 0 else "0%",
        delta_color="normal" # 美股慣例: 正值(漲)為綠色
    )
    st.caption(f"最後更新時間: {data['updated_at'].strftime('%Y-%m-%d %H:%M:%S')} {'(🟢 即時)' if data['has_live_data'] else '(🔴 舊資料)'}")

    # --- 分頁內容 ---
    tab1, tab2, tab3 = st.tabs(["📋 持倉明細", "📈 資產走勢", "🧩 族群分析"])

    # 美股顏色樣式 (綠漲紅跌)
    def style_us_colors(val):
        if val > 0: return 'color: #09ab3b; font-weight: bold' # Green
        elif val < 0: return 'color: #ff4b4b; font-weight: bold' # Red
        return ''

    with tab1:
        display_df = summary_df.style.format({
            '股數': '{:,.0f}',
            '平均成本': '{:,.2f}',
            '最新報價': '{:,.2f}',
            '當前市值': '{:,.2f}',
            '總成本': '{:,.2f}',
            '未實現損益': '{:,.2f}',
            '報酬率': '{:.2%}',
            '總占比': '{:.2%}'
        }).map(style_us_colors, subset=['未實現損益', '報酬率'])
        st.dataframe(display_df, width='stretch', height=500)

    with tab2:
        if df_asset_hist is not None and not df_asset_hist.empty:
            fig = px.line(df_asset_hist, x='日期', y='總價值', title='資產總值成長趨勢 (USD)', markers=True, text='總價值')
            fig.update_traces(textposition="top center", texttemplate='%{y:,.0f}', line_color='#1f77b4')
            fig.update_layout(hovermode="x unified")
            st.plotly_chart(fig, width='stretch')
            with st.expander("查看詳細歷史數據"):
                st.dataframe(df_asset_hist.sort_values('日期', ascending=False), width='stretch')
        else:
            st.info("尚無資產歷史紀錄。")

    with tab3:
        if '族群' in summary_df.columns:
            group_df = summary_df.groupby('族群')[['當前市值', '未實現損益', '總成本']].sum()
            group_df['報酬率'] = group_df.apply(lambda x: x['未實現損益'] / x['總成本'] if x['總成本'] != 0 else 0, axis=1)
            group_df['總佔比'] = group_df.apply(
                lambda x: x['當前市值'] / data['total_assets'] if data['total_assets'] != 0 else 0, axis=1
            )

            st.write("### 資產配置清單")
            target_cols = ['當前市值', '總佔比', '報酬率']
            display_df = group_df[target_cols]
            st.dataframe(display_df.style.format({
                '當前市值': '{:,.0f}',
                '總佔比': '{:,.1%}',
                '報酬率': '{:.1%}'
            }).map(style_us_colors, subset=['報酬率']), width='stretch')

            st.markdown("---") # 分隔線

            # --- 下方：顯示圓餅圖 ---
            st.write("### 總佔比分佈")
            # 圓餅圖
            fig_pie = px.pie(summary_df, values='當前市值', names='族群', title='投資族群配置', hole=0.4)
            st.plotly_chart(fig_pie, width='stretch')
        else:
            st.warning("CSV 中缺少「族群」欄位，無法進行分析。")

if __name__ == "__main__":
    main()