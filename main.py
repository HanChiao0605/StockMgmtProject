import streamlit as st
import subprocess
import sys

# 1. 全局設定必須在最前面
st.set_page_config(page_title="資產管理系統", layout="wide")

# 2. 啟動背景任務的邏輯 (維持不變)
@st.cache_resource
def start_background_tasks():
    """
    啟動三個獨立的資產管理腳本作為背景進程。
    這段代碼在 Streamlit 伺服器重啟前只會執行一次。
    """
    proc_futures = subprocess.Popen([sys.executable, '-u', "future_mgmt_task.py"])
    
    # proc_stocks = subprocess.Popen([sys.executable, "stocks_asset_app_bg.py"])
    # proc_stocks_us = subprocess.Popen([sys.executable, "stocks_asset_app_US_bg.py"])
    
    return {
        "期貨資產監控 (Futures)": proc_futures,
        # "台股資產監控 (Stocks)": proc_stocks,
        # "美股資產監控 (US Stocks)": proc_stocks_us
    }

# --- 3. 將監控畫面包裝成一個獨立的頁面函式 ---
def monitor_dashboard():
    st.title("📊 系統主控台 (背景任務監控)")
    st.info("背景任務已啟動！請從左側側邊欄切換至對應的頁面查看詳細資產數據。")

    # 取得進程狀態
    processes = start_background_tasks()

    st.write("### 背景進程狀態")
    for name, proc in processes.items():
        status = "🟢 運行中" if proc.poll() is None else f"🔴 已停止 (Exit code: {proc.poll()})"
        st.write(f"- **{name}**: {status} `(PID: {proc.pid})`")

    if st.button("🔄 重新整理狀態"):
        st.rerun()

    # (可選) 加上手動重啟按鈕，方便開發 Debug
    if st.button("🛑 強制停止並重置進程"):
        for name, proc in processes.items():
            proc.terminate()
        st.cache_resource.clear()
        st.warning("已送出停止指令，請再次點擊「重新整理狀態」。")


# --- 4. 設定 Navigation 導航選單 ---
# 使用 st.Page() 註冊頁面。第一個參數可以是一個「函式」，也可以是「檔案路徑」
pg = st.navigation([
    # 第一頁：系統監控 (直接呼叫上方的函式)
    st.Page(monitor_dashboard, title="系統主控台", icon="⚙️"),
    
    # 第二頁：期貨分析 (指向你的 UI 腳本檔案)
    st.Page("futures_dashboard.py", title="期貨分析", icon="🔮"),
    
    # 預留未來的頁面
    # st.Page("pages/stocks_dashboard.py", title="台股分析", icon="📈"),
    # st.Page("pages/stocks_dashboard_US.py", title="美股分析", icon="🇺🇸"),
])

# 5. 執行導航 (這行會根據你在側邊欄點選的項目，決定要渲染哪個頁面的內容)
pg.run()