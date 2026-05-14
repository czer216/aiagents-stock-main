import pandas as pd
import streamlit as st


def render_user_admin_page(auth_db, require_admin, deactivate_nav):
    st.subheader("👥 用户管理")

    if not require_admin():
        st.error("权限不足：仅管理员可访问用户管理")
        return

    with st.form("create_user_form"):
        new_username = st.text_input("新用户名", key="create_user_name")
        new_password = st.text_input("新用户密码", type="password", key="create_user_password")
        new_password_confirm = st.text_input("确认密码", type="password", key="create_user_password_confirm")
        new_role = st.selectbox("角色", options=["user", "admin"], format_func=lambda x: "普通用户" if x == "user" else "管理员", key="create_user_role")
        create_submit = st.form_submit_button("➕ 创建用户", type="primary")

    if create_submit:
        uname = str(new_username or "").strip()
        if not uname:
            st.error("请输入用户名")
        elif len(new_password) < 6:
            st.error("密码至少6位")
        elif new_password != new_password_confirm:
            st.error("两次密码不一致")
        elif auth_db.get_user_by_username(uname):
            st.error("用户名已存在")
        else:
            try:
                auth_db.create_user(username=uname, password=new_password, role=new_role)
                st.success(f"✅ 用户创建成功: {uname} ({'管理员' if new_role == 'admin' else '普通用户'})")
            except Exception as e:
                st.error(f"创建用户失败: {str(e)}")

    users = auth_db.list_users()
    if users:
        users_table = pd.DataFrame(users)
        users_table = users_table.rename(columns={
            "id": "ID",
            "username": "用户名",
            "role": "角色",
            "is_active": "状态",
            "created_at": "创建时间",
            "updated_at": "更新时间",
            "last_login_at": "最近登录",
        })
        if "角色" in users_table.columns:
            users_table["角色"] = users_table["角色"].apply(lambda x: "管理员" if str(x) == "admin" else "普通用户")
        if "状态" in users_table.columns:
            users_table["状态"] = users_table["状态"].apply(lambda x: "启用" if int(x or 0) == 1 else "禁用")
        st.dataframe(users_table, use_container_width=True, height=320)

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("⬅️ 返回", width='stretch', key="user_admin_back"):
            deactivate_nav('show_user_admin')
            st.rerun()
