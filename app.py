import gspread
from google.oauth2.service_account import Credentials
import streamlit as st
import pandas as pd
import numpy as np
import uuid

# ==================================================================
# 【★ここに後で数値を入力★】 管理者・データ設定エリア
# ==================================================================
TOTAL_STUDENTS = 130  # 学科の想定総学生数（だいたい130人）

# 1. 研究室のリスト
LABS = ["蓮池研究室", "野中研究室", "大森研究室", "後藤研究室", "小松原研究室","斎藤研究室","高橋研究室","竹本研究室","谷水研究室","檀研究室","平井研究室","棟近研究室","福重研究室"]

# 2. 91件のアンケートに基づく「第1希望の志望比率」（合計が1.0になるようにパーセンテージを小数にする）
# 例: Aが30%, Bが20%, Cが20%, Dが15%, Eが15% の場合 -> [0.30, 0.20, 0.20, 0.15, 0.15]
LAB_PROBS = [0.172, 0.075, 0.183, 0.075, 0.022, 0.086, 0.032, 0.022, 0.022, 0.043, 0.129, 0.129,0.102 ]

# 3. 昨年度のGPA分布（ヒストグラムの階級と、それぞれの割合。合計が1.0になるようにする）
GPA_BINS = ["3.5-4.0", "3.0-3.5", "2.5-3.0", "2.0-2.5", "1.5-2.0","1.0-1.5","0.5-1.0","0.0-0.5"]
GPA_PROBS = [0.059, 0.297, 0.287, 0.248, 0.059, 0.040, 0.010, 0.000] 

# 4. 各研究室の定員設定
MAX_CAPACITY = 10      # 総定員
MERIT_CAPACITY = 7    # 優秀者優先枠

ADMIN_PASSWORD = "kyoshitsu-mae-kisyokisyo"  # 管理者パスワード

# ==================================================================
# 【新設】Google Sheets 安全通信ブロック（行追加方式で紛失リスクを最小化）
# ==================================================================
def get_gsheet_worksheet():
    """StreamlitのSecrets情報を使って安全にGoogle Sheetsへ接続する"""
    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    # 認証情報をサーバーの隠しファイル(Secrets)から取得
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    # 鍵を指定してスプレッドシートの1枚目のシートを開く
    sheet = client.open_by_key(st.secrets["spreadsheet_key"]).sheet1
    return sheet

def load_real_data():
    """Google Sheetsから最新の実際の回答データをリアルタイムに読み込む（デバッグ版）"""
    try:
        sheet = get_gsheet_worksheet()
        records = sheet.get_all_records()
        return pd.DataFrame(records)
    except Exception as e:
        # 【デバッグ用】何のエラーが発生して0件になっているのかを画面に赤文字で出す
        st.error(f"⚠️ Google Sheetsとの通信でエラーが発生しています: {e}")
        return pd.DataFrame(columns=[
            "シリアルID", "GPA", "第1希望", 
            "第2希望_1", "第2希望_2", "第2希望_3", 
            "第3希望_1", "第3希望_2", "第3希望_3", "第3希望_4", "第3希望_5"
        ])

# ==================================================================
# 2. ページ設定とシステム初期化
# ==================================================================
st.set_page_config(page_title="研究室配属シミュレーター", layout="centered")
st.title("研究室配属 中間発表シミュレーター")
st.caption("※本システムは完全匿名です．学籍番号などの個人情報は一切収集しません．")

if "db" not in st.session_state:
    st.session_state.db = pd.DataFrame(columns=[
        "シリアルID", "GPA", "第1希望", 
        "第2希望_1", "第2希望_2", "第2希望_3", 
        "第3希望_1", "第3希望_2", "第3希望_3", "第3希望_4", "第3希望_5"
    ])

if "submitted" not in st.session_state:
    st.session_state.submitted = False
# アプリ起動時に、このセッション専用のシリアルIDを1つに固定（再提出時の紐付け鍵にする）
if "my_temporary_id" not in st.session_state or st.session_state.my_temporary_id == "":
    st.session_state.my_temporary_id = f"User_{uuid.uuid4().hex[:6].upper()}"
if "my_p1" not in st.session_state:
    st.session_state.my_p1 = ""

# ==================================================================
# 3. コアロジック関数群（傾斜補正および全体配属ドラフトアルゴリズム）
# ==================================================================

def generate_virtual_students(num_needed):
    """GPAの高さに応じて志望人気比率の偏りを動的に変化させる仮想学生生成ロジック（ボーダー3.4再現版）"""
    if num_needed <= 0:
        return pd.DataFrame()
    
    # GPA全体の確率の厳密な正規化
    normalized_gpa_probs = np.array(GPA_PROBS, dtype=np.float64)
    if normalized_gpa_probs.sum() > 0:
        normalized_gpa_probs = normalized_gpa_probs / normalized_gpa_probs.sum()
        normalized_gpa_probs[-1] = 1.0 - normalized_gpa_probs[:-1].sum()
    
    # ベースとなる研究室人気比率の正規化
    base_lab_probs = np.array(LAB_PROBS, dtype=np.float64)
    base_lab_probs = base_lab_probs / base_lab_probs.sum()
    
    # 完全にフラットな均等確率（ドラフト回避行動のベース）
    uniform_probs = np.ones(len(LABS), dtype=np.float64) / len(LABS)
    
    virtual_rows = []
    for i in range(num_needed):
        # 1. 過去の分布からGPAを生成（最高階級には右下がりの傾斜を適用）
        chosen_bin = np.random.choice(GPA_BINS, p=normalized_gpa_probs)
        low, high = map(float, chosen_bin.split("-"))
        
        if low >= 3.5:
            gpa = round(high - (np.sqrt(np.random.uniform(0, 1)) * (high - low)), 2)
        else:
            gpa = round(np.random.uniform(low, high), 2)
        
        # 2. GPA連動型の志望研究室決定ロジック
        if gpa >= 3.4:
            # 【チューニング】3.4以上の学生は、アンケート上位の人気研究室へ極端に集中する（確率を3乗して格差を拡大）
            # これにより、蓮池研・大森研の優秀者優先枠（最初の7人）のボーダーが3.4付近に引き締まります．
            sharp_probs = base_lab_probs ** 3.0
            adjusted_lab_probs = sharp_probs / sharp_probs.sum()
        else:
            # 3.4未満の層は、GPAが低くなるほどドラフトを警戒して志望がフラットに分散する
            weight = (gpa / 3.4)
            adjusted_lab_probs = weight * base_lab_probs + (1.0 - weight) * uniform_probs
        
        # 浮動小数点誤差の完全排除
        adjusted_lab_probs = adjusted_lab_probs / adjusted_lab_probs.sum()
        adjusted_lab_probs[-1] = 1.0 - adjusted_lab_probs[:-1].sum()
        
        p1 = np.random.choice(LABS, p=adjusted_lab_probs)
        
        # 第2・第3希望は重複しないように仮割り当て
        remaining_labs = [l for l in LABS if l != p1]
        p2 = list(np.random.choice(remaining_labs, 3, replace=False))
        p3 = [l for l in remaining_labs if l not in p2]
        
        virtual_rows.append({
            "シリアルID": f"Virtual_{i+1:03d}", "GPA": gpa, "第1希望": p1,
            "第2希望_1": p2[0], "第2希望_2": p2[1], "第2希望_3": p2[2],
            "第3希望_1": p3[0], "第3希望_2": p3[1], "第3希望_3": "", "第3希望_4": "", "第3希望_5": ""
        })
    return pd.DataFrame(virtual_rows)


def run_allocation_simulation(real_df, fixed_virtual_df=None):
    """成績優秀者枠7割（高GPA順配属）と学科指定枠（低GPA順ドラフト・仕様B平均平準化案）を組み合わせた全体配属アルゴリズム"""
    if fixed_virtual_df is not None:
        virtual_df = fixed_virtual_df
    else:
        num_real = len(real_df)
        num_virtual_needed = max(0, TOTAL_STUDENTS - num_real)
        virtual_df = generate_virtual_students(num_virtual_needed)
        
    blended_df = pd.concat([real_df, virtual_df], ignore_index=True)
    df_sorted = blended_df.sort_values(by="GPA", ascending=False).copy()
    total = len(df_sorted)
    
    merit_cutoff = int(np.ceil(total * 2 / 3))
    df_sorted["グループ"] = ["成績優秀者" if i < merit_cutoff else "一般" for i in range(total)]
    
    allocation = {lab: [] for lab in LABS}
    unallocated = []
    
    # 【新設】各研究室の現時点での配属者の平均GPAを計算するインラインヘルパー関数
    def get_lab_mean_gpa(lab_name):
        lab_students = allocation[lab_name]
        if not lab_students:
            return 0.0  # まだ誰も配属されていない場合は0.0
        return df_sorted[df_sorted["シリアルID"].isin(lab_students)]["GPA"].mean()
    
    # --- フェーズ1: 成績優秀者の配属（高GPA順） ---
    df_merit = df_sorted[df_sorted["グループ"] == "成績優秀者"]
    for _, student in df_merit.iterrows():
        s_id = student["シリアルID"]
        p1 = student["第1希望"]
        p2_list = [student[f"第2希望_{i}"] for i in range(1, 4) if student[f"第2希望_{i}"]]
        p3_list = [student[f"第3希望_{i}"] for i in range(1, 6) if student[f"第3希望_{i}"]]
        
        if p1 in allocation and len(allocation[p1]) < MERIT_CAPACITY:
            allocation[p1].append(s_id)
            continue
        available_p2 = [lab for lab in p2_list if lab in allocation and len(allocation[lab]) < MERIT_CAPACITY]
        if available_p2:
            best_p2 = min(available_p2, key=lambda x: len(allocation[x]))
            allocation[best_p2].append(s_id)
            continue
        available_p3 = [lab for lab in p3_list if lab in allocation and len(allocation[lab]) < MERIT_CAPACITY]
        if available_p3:
            best_p3 = min(available_p3, key=lambda x: len(allocation[x]))
            allocation[best_p3].append(s_id)
            continue
        unallocated.append(student)
        
    # --- フェーズ2: 学科指定枠の配属（低GPA順ドラフト・仕様B平準化） ---
    df_general = df_sorted[df_sorted["グループ"] == "一般"]
    phase2_students = pd.concat([df_general, pd.DataFrame(unallocated)]).drop_duplicates(subset=["シリアルID"]) if unallocated else df_general.copy()
    
    # 【仕様通り】低GPA順（昇順）にソートして、成績が低い学生から選考
    phase2_students = phase2_students.sort_values(by="GPA", ascending=True)
    
    final_overflow = []
    for _, student in phase2_students.iterrows():
        s_id = student["シリアルID"]
        p1 = student["第1希望"]
        p2_list = [student[f"第2希望_{i}"] for i in range(1, 4) if student[f"第2希望_{i}"]]
        p3_list = [student[f"第3希望_{i}"] for i in range(1, 6) if student[f"第3希望_{i}"]]
        
        # (1) 第1希望が空いていれば、選択の余地なく配属
        if p1 in allocation and len(allocation[p1]) < MAX_CAPACITY:
            allocation[p1].append(s_id)
            continue
            
        # (2) 第2希望の選考（定員に空きがある候補の中から、その時点で「最も平均GPAが高い」研究室を動的に選択）
        available_p2 = [lab for lab in p2_list if len(allocation[lab]) < MAX_CAPACITY]
        if available_p2:
            best_p2 = max(available_p2, key=get_lab_mean_gpa)
            allocation[best_p2].append(s_id)
            continue
            
        # (3) 第3希望の選考（同様に、空き候補の中から「最も平均GPAが高い」研究室を選択）
        available_p3 = [lab for lab in p3_list if len(allocation[lab]) < MAX_CAPACITY]
        if available_p3:
            best_p3 = max(available_p3, key=get_lab_mean_gpa)
            allocation[best_p3].append(s_id)
            continue
            
        # 全ての希望が満員だった場合は救済枠へ
        final_overflow.append(student)
        
    # 希望枠から完全に溢れた学生の最終配属（空きがあり、最も平均GPAが高い研究室へ機械的配属）
    for student in final_overflow:
        s_id = student["シリアルID"]
        available_labs = [lab for lab in LABS if len(allocation[lab]) < MAX_CAPACITY]
        if available_labs:
            best_lab = max(available_labs, key=get_lab_mean_gpa)
            allocation[best_lab].append(s_id)
            
    return allocation, df_sorted


def calculate_ranks_with_virtual(real_df, target_id, target_lab, fixed_virtual):
    """固定された同一の母集団から、順位・ボーダーラインを算出する"""
    blended_df = pd.concat([real_df, fixed_virtual], ignore_index=True)
    blended_df = blended_df.sort_values(by="GPA", ascending=False).reset_index(drop=True)
    
    blended_df["全体順位"] = blended_df["GPA"].rank(ascending=False, method="min").astype(int)
    
    merit_cutoff_idx = int(np.ceil(len(blended_df) * 2 / 3)) - 1
    border_gpa = blended_df.loc[merit_cutoff_idx, "GPA"]
    
    df_pref1 = blended_df[blended_df["第1希望"] == target_lab].copy()
    df_pref1["順位"] = df_pref1["GPA"].rank(ascending=False, method="min").astype(int)
    
    rank_total = blended_df[blended_df["シリアルID"] == target_id]["全体順位"].values[0]
    rank_pref1 = df_pref1[df_pref1["シリアルID"] == target_id]["順位"].values[0]
    
    return rank_pref1, len(df_pref1), rank_total, len(blended_df), border_gpa

# ==================================================================
# 4. 画面UI構成
# ==================================================================
tab1, tab2 = st.tabs(["学生用：回答・暫定順位・合否シミュレーション", "管理者用：中間発表・リセット"])

with tab1:
    st.header("希望研究室・GPAの入力")
    
    if st.session_state.submitted:
        st.success("あなたのデータは匿名で送信されました．")
        
        # 公正性を担保するため、順位計算と配属シミュレーションで「全く同一の仮想ライバル集団」を固定して使用する
        if "fixed_virtual_df" not in st.session_state or len(st.session_state.fixed_virtual_df) != max(0, TOTAL_STUDENTS - len(st.session_state.db)):
            num_real = len(st.session_state.db)
            num_virtual_needed = max(0, TOTAL_STUDENTS - num_real)
            st.session_state.fixed_virtual_df = generate_virtual_students(num_virtual_needed)
            
        # 1. 順位とボーダーの計算
        real_df = load_real_data()  # 通信して最新の回答一覧をロード
        r1, t1, r3, t3, border_gpa = calculate_ranks_with_virtual(
            real_df, st.session_state.my_temporary_id, st.session_state.my_p1, st.session_state.fixed_virtual_df
        )
        res_alloc, df_proc = run_allocation_simulation(real_df, st.session_state.fixed_virtual_df)        
        # 自分がどの研究室に配属されたかを特定
        my_allocated_lab = "未配属（エラー）"
        for lab, students in res_alloc.items():
            if st.session_state.my_temporary_id in students:
                my_allocated_lab = lab
                break
                
        # 自分の入力データを取得して、配属先が第何希望にあたるかを判定
        my_row = real_df[real_df["シリアルID"] == st.session_state.my_temporary_id].iloc[0]
        p1 = my_row["第1希望"]
        p2_list = [my_row[f"第2希望_{i}"] for i in range(1, 4) if my_row[f"第2希望_{i}"]]
        p3_list = [my_row[f"第3希望_{i}"] for i in range(1, 6) if my_row[f"第3希望_{i}"]]
        
        if my_allocated_lab == p1:
            wish_rank_text = "【第1希望】"
            alert_style = st.success
        elif my_allocated_lab in p2_list:
            wish_rank_text = "【第2希望（スライド）】"
            alert_style = st.info
        elif my_allocated_lab in p3_list:
            wish_rank_text = "【第3希望（スライド）】"
            alert_style = st.warning
        else:
            wish_rank_text = "【希望外の研究室（ドラフト溢れ救済）】"
            alert_style = st.error

        # --- 画面表示エリア ---
        st.markdown("### あなたの配属シミュレーション結果")
        alert_style(f"現在の暫定内定先： **{my_allocated_lab}** ({wish_rank_text})")
        
        st.markdown("---")
        st.markdown("###  あなたの現在の暫定順位（過去データ補完）")
        col1, col2 = st.columns(2)
        with col1:
            st.metric(label=f"{st.session_state.my_p1} の「第1希望者内」順位", value=f"{r1} 位", delta=f"想定ライバル: {t1} 人中")
        with col2:
            st.metric(label="学科内での「純粋なGPA」全体順位", value=f"{r3} 位", )
            
        st.markdown("---")
        st.markdown("### 上位2/3ボーダーの目安")
        st.metric(label="上位2/3ボーダーラインGPA", value=f"{border_gpa:.2f}")
        
        my_gpa = my_row["GPA"]
        if my_gpa >= border_gpa:
            st.info("💡 あなたのGPAは、現在の優秀者枠のボーダーラインを超えています．第1〜第3希望に書いた研究室のいずれかに、成績順で優先配属される権利を持っています．")
        else:
            st.warning("💡 あなたのGPAは、現在の優秀者枠のボーダーラインを下回っています．「学科指定枠（低GPA順ドラフト）」の選考に回るため、第1希望が落ちた場合は第2・第3希望に書いた研究室の中からドラフト（空き数が多く人気が低い研究室が優先）されます．")
            
        st.caption(f"※現在の実際の回答数: {len(st.session_state.db)} 件．不足分は昨年度の分布に基づく仮想データです．")
        
        if st.button("新しく入力をやり直す (デバッグ用)"):
            st.session_state.submitted = False
            if "fixed_virtual_df" in st.session_state:
                del st.session_state.fixed_virtual_df
            st.rerun()
            
    else:
        with st.form("anonymous_form"):
            gpa = st.number_input("自身の合計GPA (半角数字)", min_value=0.00, max_value=4.50, value=3.00, step=0.01, format="%.2f")
            p1 = st.selectbox("第1希望研究室", LABS)
            
            st.markdown("**第2希望研究室 (最大3つ)**")
            c2_1, c2_2, c2_3 = st.columns(3)
            with c2_1: p2_1 = st.selectbox("第2希望-1", [""] + LABS)
            with c2_2: p2_2 = st.selectbox("第2希望-2", [""] + LABS)
            with c2_3: p2_3 = st.selectbox("第2希望-3", [""] + LABS)
                
            st.markdown("**第3希望研究室 (最大5つ)**")
            c3_1, c3_2, c3_3, c3_4, c3_5 = st.columns(5)
            with c3_1: p3_1 = st.selectbox("第3希望-1", [""] + LABS)
            with c3_2: p3_2 = st.selectbox("第3希望-2", [""] + LABS)
            with c3_3: p3_3 = st.selectbox("第3希望-3", [""] + LABS)
            with c3_4: p3_4 = st.selectbox("第3希望-4", [""] + LABS)
            with c3_5: p3_5 = st.selectbox("第3希望-5", [""] + LABS)
            
            submit_btn = st.form_submit_button("匿名でデータを送信して配属シミュレーションを実行")
            
        # --- 修正後（送信ボタンが押された瞬間の処理） ---
# --- 修正後：バリデーション＆自動上書き更新機能付き送信処理 ---
        if submit_btn:
            # ① 重複バリデーションの実行
            selected_labs = [p1, p2_1, p2_2, p2_3, p3_1, p3_2, p3_3, p3_4, p3_5]
            # ユーザーが選択した「空欄以外の有効な研究室名」だけを抽出
            valid_selected_labs = [l for l in selected_labs if l != ""]
            
            if len(valid_selected_labs) != len(set(valid_selected_labs)):
                # 重複がある場合は赤文字で警告を出し、送信処理を完全にブロックする
                st.error("🚨 希望研究室の選択に重複があります．同じ研究室を複数回選択することはできません．")
            else:
                # 重複がない場合のみ、Google Sheetsへの通信処理へ進む
                temp_id = st.session_state.my_temporary_id
                new_row_data = [
                    temp_id, float(gpa), p1,
                    p2_1, p2_2, p2_3,
                    p3_1, p3_2, p3_3, p3_4, p3_5
                ]
                
                try:
                    sheet = get_gsheet_worksheet()
                    all_values = sheet.get_all_values()
                    
                    # 万が一シートが完全に真っ白な場合はヘッダーを自動生成
                    if len(all_values) == 0:
                        sheet.append_row([
                            "シリアルID", "GPA", "第1希望", 
                            "第2希望_1", "第2希望_2", "第2希望_3", 
                            "第3希望_1", "第3希望_2", "第3希望_3", "第3希望_4", "第3希望_5"
                        ])
                        all_values = sheet.get_all_values()
                    
                    # ② 再提出（上書き更新）への対応ロジック
                    # 既にGoogle Sheets上に自分のシリアルIDが存在するか行単位で走査
                    serial_col_idx = 0  # 1列目がシリアルID
                    existing_row_num = None
                    
                    for idx, row in enumerate(all_values):
                        if row and row[serial_col_idx] == temp_id:
                            existing_row_num = idx + 1  # Google Sheetsの行番号は1始まりのため+1
                            break
                    
                    if existing_row_num is not None:
                        # 【上書き更新】過去に提出済みなら、その行を今回の最新データで上書き（データ増殖を防止）
                        range_name = f"A{existing_row_num}:K{existing_row_num}"
                        sheet.update(range_name, [new_row_data])
                        st.toast("以前提出したデータを最新情報に更新しました．", icon="🔄")
                    else:
                        # 【新規追加】初めての提出なら、末尾に1行新規追加
                        sheet.append_row(new_row_data)
                        st.toast("データを新規送信しました．", icon="✅")
                    
                    # 入力状態を保持して画面を切り替える
                    st.session_state.submitted = True
                    st.session_state.my_p1 = p1
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"データの送信・更新に失敗しました．通信環境の良い場所でもう一度お試しください．(Error: {e})")
with tab2:
    st.header("管理者専用メニュー")
    input_pass = st.text_input("管理者パスワードを入力してください", type="password")
    
    if input_pass == ADMIN_PASSWORD:
        real_df = load_real_data()  # 最新データをロード
        st.success("認証成功：管理権限が有効です．")
        st.write(f"現在の実際の回答数: {len(real_df)} 件")
        
        col_act1, col_act2 = st.columns(2)
        with col_act1:
            if st.button("中間発表（全体配属）を実行する", type="primary"):
                res_alloc, df_proc = run_allocation_simulation(real_df) # メモリではなくリアルデータで集計
                
                # --- 【新設】検証用サマリーデータの集計ロジック ---
                summary_rows = []
                for lab in LABS:
                    students = res_alloc.get(lab, [])
                    if students:
                        sub = df_proc[df_proc["シリアルID"].isin(students)]
                        max_gpa = sub["GPA"].max()
                        min_gpa = sub["GPA"].min()
                        mean_gpa = sub["GPA"].mean()
                        # 枠ごとの人数カウント
                        merit_cnt = len(sub[sub["グループ"] == "成績優秀者"])
                        general_cnt = len(sub[sub["グループ"] == "一般"])
                    else:
                        max_gpa = min_gpa = mean_gpa = merit_cnt = general_cnt = 0
                        
                    summary_rows.append({
                        "研究室名": lab,
                        "配属人数": len(students),
                        "最高GPA": round(max_gpa, 2),
                        "最低GPA": round(min_gpa, 2),
                        "平均GPA": round(mean_gpa, 2),
                        "優秀者枠(上位2/3)からの配属数": merit_cnt,
                        "学科指定枠(下位1/3・スライド)からの配属数": general_cnt
                    })
                summary_df = pd.DataFrame(summary_rows)
                
                st.markdown("### 📊 アルゴリズム検証用サマリー（ここをチェック）")
                st.dataframe(summary_df, use_container_width=True)
                
                st.markdown("---")
                st.markdown("### 🏫 各研究室の配属内定者詳細名簿")
                for lab, students in res_alloc.items():
                    st.markdown(f"**【{lab}】** (配属確定: {len(students)} / {MAX_CAPACITY}名)")
                    if students:
                        sub = df_proc[df_proc["シリアルID"].isin(students)][["シリアルID", "GPA", "グループ", "第1希望"]]
                        st.dataframe(sub, use_container_width=True)
                        
        with col_act2:
            if st.button("🚨 データをすべて削除 (夏のリセット用)", type="secondary"):
                try:
                    sheet = get_gsheet_worksheet()
                    sheet.clear()  # 全削除
                    # ヘッダーのみ再配置
                    sheet.append_row([
                        "シリアルID", "GPA", "第1希望", 
                        "第2希望_1", "第2希望_2", "第2希望_3", 
                        "第3希望_1", "第3希望_2", "第3希望_3", "第3希望_4", "第3希望_5"
                    ])
                    st.session_state.submitted = False
                    if "fixed_virtual_df" in st.session_state:
                        del st.session_state.fixed_virtual_df
                    st.warning("Google Sheets上の蓄積データをすべて消去しました．")
                    st.rerun()
                except Exception as e:
                    st.error(f"リセットに失敗しました: {e}")
                
        st.subheader("現在蓄積されているローデータ（回答順）")
        st.dataframe(real_df, use_container_width=True) # リアルデータを表示
        
    elif input_pass != "":
        st.error("パスワードが一致しません．")