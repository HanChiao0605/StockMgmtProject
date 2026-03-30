import streamlit as st

pg = st.navigation([
	st.Page("futures_asset_app.py", title="期貨分析", icon="🔮"),
	st.Page("stocks_asset_app.py", title="股票分析", icon="📈"),
	st.Page("stocks_asset_app_US.py", title="股票分析_US", icon="📈"),
])
pg.run()