import streamlit as st


def inject_global_styles() -> None:
    st.markdown(
        """
<style>
    .main {
        background: linear-gradient(135deg, #e8f5f3 0%, #d4ebe8 50%, #e0f2f1 100%) !important;
        background-attachment: fixed;
    }

    .stApp {
        background: transparent !important;
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        padding-left: 2rem;
        padding-right: 2rem;
        max-width: 100%;
        background: transparent;
        border-radius: 0;
        box-shadow: none;
        margin-top: 0;
    }

    .top-nav {
        background: white;
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 2rem;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        border: 1px solid #e8eaed;
    }

    .nav-title {
        font-size: 1.8rem;
        font-weight: 700;
        color: #202124;
        text-align: center;
        margin: 0;
        text-shadow: none;
        letter-spacing: 0;
    }

    .nav-subtitle {
        text-align: center;
        color: #5f6368;
        font-size: 0.9rem;
        margin-top: 0.5rem;
        font-weight: 400;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
        background: white;
        padding: 0.75rem 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        border: 1px solid #e8eaed;
    }

    .stTabs [data-baseweb="tab"] {
        height: 48px;
        background: transparent;
        border-radius: 8px;
        color: #5f6368;
        font-weight: 500;
        font-size: 0.95rem;
        padding: 0 1.5rem;
        border: none;
        transition: all 0.2s ease;
    }

    .stTabs [data-baseweb="tab"]:hover {
        background: #f8f9fa;
        color: #202124;
    }

    .stTabs [aria-selected="true"] {
        background: #e8f5e9 !important;
        color: #1e8e3e !important;
        font-weight: 600;
    }

    .css-1d391kg, [data-testid="stSidebar"] {
        background: #ffffff;
        padding-top: 1.5rem;
        border-right: 1px solid #e8eaed;
    }

    .css-1d391kg h1, .css-1d391kg h2, .css-1d391kg h3,
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
        color: #202124 !important;
    }

    .css-1d391kg .stMarkdown, [data-testid="stSidebar"] .stMarkdown {
        color: #5f6368 !important;
    }

    [data-testid="stSidebar"] .stButton>button {
        background: transparent;
        color: #5f6368;
        border: none;
        border-radius: 8px;
        padding: 0.65rem 1rem;
        font-weight: 500;
        font-size: 0.9rem;
        text-align: left;
        box-shadow: none;
        transition: all 0.2s ease;
    }

    [data-testid="stSidebar"] .stButton>button:hover {
        background: #f8f9fa;
        color: #202124;
        transform: none;
        box-shadow: none;
    }

    [data-testid="stSidebar"] .stButton>button:active,
    [data-testid="stSidebar"] .stButton>button:focus {
        background: #e8f5e9;
        color: #1e8e3e;
        box-shadow: none;
    }

    .agent-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        margin: 1rem 0;
        border: 1px solid #e8eaed;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        transition: all 0.2s ease;
    }

    .agent-card:hover {
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        transform: translateY(-2px);
    }

    .decision-card {
        background: white;
        padding: 2rem;
        border-radius: 12px;
        border: 2px solid #34a853;
        margin: 1.5rem 0;
        box-shadow: 0 2px 8px rgba(52, 168, 83, 0.1);
    }

    .warning-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        border-left: 4px solid #fbbc04;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
    }

    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        text-align: center;
        transition: all 0.2s ease;
        border: 1px solid #e8eaed;
    }

    .metric-card:hover {
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        transform: translateY(-2px);
    }

    .stButton>button {
        background: #1a73e8;
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.65rem 1.8rem;
        font-weight: 600;
        font-size: 0.95rem;
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 2px 4px rgba(26, 115, 232, 0.2);
        letter-spacing: 0.3px;
    }

    .stButton>button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 8px rgba(26, 115, 232, 0.3);
        background: #1557b0;
    }

    .stButton>button:active {
        transform: translateY(0px);
        box-shadow: 0 1px 2px rgba(26, 115, 232, 0.3);
    }

    .stTextInput>div>div>input {
        border-radius: 8px;
        border: 1px solid #dadce0;
        padding: 0.75rem;
        font-size: 0.95rem;
        transition: all 0.2s ease;
        background: white;
    }

    .stTextInput>div>div>input:focus {
        border-color: #1e8e3e;
        box-shadow: 0 0 0 2px rgba(30, 142, 62, 0.1);
    }

    .stProgress > div > div > div > div {
        background: linear-gradient(90deg, #1e8e3e 0%, #34a853 100%);
    }

    .stSuccess {
        background: #e6f4ea;
        border-radius: 8px;
        padding: 1rem;
        border-left: 4px solid #34a853;
        color: #137333;
    }

    .stError {
        background: #fce8e6;
        border-radius: 8px;
        padding: 1rem;
        border-left: 4px solid #ea4335;
        color: #c5221f;
    }

    .stWarning {
        background: #fef7e0;
        border-radius: 8px;
        padding: 1rem;
        border-left: 4px solid #fbbc04;
        color: #b06000;
    }

    .stInfo {
        background: #e8f0fe;
        border-radius: 8px;
        padding: 1rem;
        border-left: 4px solid #1a73e8;
        color: #174ea6;
    }

    .js-plotly-plot {
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        background: white;
        border: 1px solid #e8eaed;
    }

    .streamlit-expanderHeader {
        background: white;
        border-radius: 8px;
        font-weight: 500;
        border: 1px solid #e8eaed;
        color: #202124;
    }

    .dataframe {
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        border: 1px solid #e8eaed;
    }

    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    @media (max-width: 768px) {
        .nav-title {
            font-size: 1.3rem;
        }
        .stTabs [data-baseweb="tab"] {
            font-size: 0.85rem;
            padding: 0 1rem;
        }
    }
</style>
""",
        unsafe_allow_html=True,
    )
