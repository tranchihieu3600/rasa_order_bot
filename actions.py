from typing import Any, Text, Dict, List, Optional
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
import pyodbc
import json
import re
from thefuzz import process, fuzz

CONN_STR = (
    "Driver={SQL Server};"
    "Server=HIEU;"
    "Database=QuanLyQuanAn;"
    "Trusted_Connection=yes;"
)

# =================================================================================
# KHU VỰC HÀM BỔ TRỢ (HELPER FUNCTIONS)
# =================================================================================

def join_natural(items: List[str]) -> str:
    """Nối danh sách món ăn thành chuỗi tự nhiên: 'A, B và C'."""
    if not items: return ""
    if len(items) == 1: return items[0]
    return ", ".join(items[:-1]) + " và " + items[-1]

def get_location_phrase(table_name: str) -> str:
    """Trả về câu vị trí hợp lý (xử lý 'Bàn', 'Mang về')."""
    if not table_name: return "cho đơn Mang về"
    t_lower = table_name.lower()
    if "mang về" in t_lower: return "cho đơn Mang về"
    # Nếu chỉ có số (ví dụ: "10"), thêm chữ "Bàn"
    if table_name.isdigit(): return f"tại Bàn {table_name}"
    return f"tại {table_name}"

def check_reduction_keyword(text: str) -> bool:
    """Kiểm tra xem câu nói có chứa ý định giảm/bớt/xóa hay không."""
    keywords = ['bớt', 'giảm', 'trừ', 'xóa', 'bỏ', 'huy', 'huỷ']
    # Tìm từ khóa, không phân biệt hoa thường
    pattern = r'\b(' + '|'.join(keywords) + r')\b'
    return bool(re.search(pattern, text, re.IGNORECASE))

def merge_items(resolved_list: List[Dict], new_item: Dict):
    """
    Hàm GỘP món ăn (CORE LOGIC):
    1. Tìm xem món đã có trong danh sách chưa (theo idFood).
    2. Nếu có: Cộng dồn số lượng (lưu ý new_item['quantity'] có thể là số âm).
    3. Kiểm tra kết quả: Nếu số lượng <= 0 -> Xóa món khỏi danh sách.
    4. Nếu chưa có: Chỉ thêm vào nếu số lượng > 0.
    """
    found = False
    # Duyệt ngược để an toàn khi xóa phần tử (pop)
    for i in range(len(resolved_list) - 1, -1, -1):
        existing = resolved_list[i]
        if existing['idFood'] == new_item['idFood']:
            # Cộng dồn
            existing['quantity'] += new_item['quantity']
            
            # Logic: Không để số lượng âm
            if existing['quantity'] <= 0:
                resolved_list.pop(i) # Xóa luôn món đó
            
            found = True
            break
    
    # Nếu chưa có trong danh sách và số lượng thêm vào là dương thì mới thêm
    if not found and new_item['quantity'] > 0:
        resolved_list.append(new_item)

def db_connect():
    try:
        conn = pyodbc.connect(CONN_STR)
        return conn
    except Exception as e:
        print(f"DEBUG: Lỗi kết nối database: {e}")
        raise e

def clean_food_text(text):
    """Làm sạch câu nói để tìm tên món chính xác hơn."""
    # Lưu ý: KHÔNG xóa các từ 'bớt', 'giảm' ở đây để hàm check_reduction_keyword còn hoạt động.
    location_keywords = ['ở', 'tại', 'bàn', 'mang', 'về', 'nhé', 'ạ', 'cho', 'tôi', 'đi', 'nha', 'ấy', 'đó']
    unit_keywords = ['phần', 'suất', 'dĩa', 'đĩa', 'bát', 'tô', 'cái', 'ly', 'cốc', 'chai', 'lon']
    
    cleaned = text
    for keyword in location_keywords:
        pattern = rf'\s*{re.escape(keyword)}\s+.*$'
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
    for keyword in unit_keywords:
        pattern = rf'\b{re.escape(keyword)}\b'
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)

    return cleaned.strip()

def search_foods(food_raw: str) -> List[Dict]:
    """Tìm kiếm món ăn trong Database."""
    words = [w.strip() for w in re.split(r"\s+", food_raw) if w.strip()]
    
    # Loại bỏ các từ khóa hành động khỏi từ khóa tìm kiếm
    action_stops = ['bớt', 'giảm', 'trừ', 'xóa', 'bỏ', 'thêm', 'lấy']
    search_words = [w for w in words if w.lower() not in action_stops]
    
    if not search_words: return []

    conn = db_connect()
    cursor = conn.cursor()

    # Bước 0: Làm sạch cơ bản
    clean_raw = food_raw.strip()
    if not clean_raw: return []

    try:
        # =========================================================================
        # CHIẾN THUẬT 0: TÌM CHÍNH XÁC TUYỆT ĐỐI (Priority #1)
        # Khắc phục lỗi "Cơm Thêm": Nếu tên món trùng khớp 100% với input -> Lấy luôn
        # =========================================================================
        sql_exact = "SELECT idFood, foodName, price FROM Food WHERE foodName = ?" # Hoặc dùng LIKE để không phân biệt hoa thường
        cursor.execute(sql_exact, (clean_raw,))
        row = cursor.fetchone()
        if row:
            # Nếu tìm thấy đúng y chang món đó (VD: Cơm Thêm) -> Trả về ngay
            return [dict(idFood=row[0], foodName=row[1], price=float(row[2]))]

        # 1. Tìm chính xác (AND)
        sql_strict = "SELECT idFood, foodName, price FROM Food WHERE " + " AND ".join([f"foodName LIKE ?" for _ in search_words]) + " ORDER BY foodName"
        params_strict = [f"%{w}%" for w in search_words]
        cursor.execute(sql_strict, params_strict)
        rows = cursor.fetchall()
        if rows:
            return [dict(idFood=row[0], foodName=row[1], price=float(row[2])) for row in rows]

        # 2. Tìm mở rộng (OR) - bỏ từ ngắn
        valid_words = [w for w in search_words if len(w) > 1]
        if not valid_words: return []

        sql_loose = "SELECT idFood, foodName, price FROM Food WHERE " + " OR ".join([f"foodName LIKE ?" for _ in valid_words]) + " ORDER BY foodName"
        params_loose = [f"%{w}%" for w in valid_words]
        cursor.execute(sql_loose, params_loose)
        rows = cursor.fetchall()
        
        return [dict(idFood=row[0], foodName=row[1], price=float(row[2])) for row in rows][:10]

    finally:
        cursor.close()
        conn.close()

def search_food_in_resolved(food_raw: str, resolved_list: List[Dict]) -> Optional[Dict]:
    """Tìm món ăn trong danh sách resolved (đơn hàng) dựa trên tên gần đúng."""
    # Hàm này dùng thefuzz (đã import) để tìm món cũ trong đơn hàng đang pending
    
    food_raw_lower = food_raw.lower().strip()
    
    # 1. Tìm chính xác (hoặc chứa tên)
    for item in resolved_list:
        if food_raw_lower in item["food"].lower():
            return item
    
    # 2. Thử thefuzz để bắt lỗi chính tả trong tên món cũ
    food_names = [item["food"] for item in resolved_list]
    if food_names:
        # Ngưỡng cao 90% để đảm bảo đây là món cũ
        best_match = process.extractOne(food_raw_lower, food_names, scorer=fuzz.ratio) 
        if best_match and best_match[1] >= 90:  #index 1 là điểm số
            matched_name = best_match[0] #index 0 là tên món
            for item in resolved_list:
                if item["food"] == matched_name:
                    return item
    return None

def find_table_by_text(text: str) -> str:
    """
    Tìm thông tin bàn. 
    Logic: Ưu tiên tìm số bàn. Nếu không có số bàn -> Mặc định là 'Mang về'.
    """
    t = text.lower().strip()
    
    # BƯỚC 1: Cố gắng tìm số bàn bằng Regex (Ưu tiên cao nhất)
    # Regex 1: Bắt trường hợp có chữ "vip" (Ví dụ: Bàn vip 1)
    m = re.search(r"bàn\s*(vip\s*)?(\d+)", t, flags=re.IGNORECASE)
    if m:
        prefix = 'Bàn Vip ' if m.group(1) else 'Bàn '
        return prefix + m.group(2)
    
    # Regex 2: Bắt trường hợp bàn thường (Ví dụ: Bàn 5, ban 5)
    m2 = re.search(r"bàn\s*(\d+)", t, flags=re.IGNORECASE)
    if m2: 
        return 'Bàn ' + m2.group(1)

    # BƯỚC 2: Nếu chạy hết các regex trên mà không thấy số bàn nào
    # -> Mặc định trả về "Mang về" (bất kể có từ 'mang' hay không)
    return 'Mang về'

def load_current_order_from_db(table_name: str) -> List[Dict]:
    """
    Load đơn hàng từ SQL dựa trên schema: 
    TableFood(idTable, tableName), Bill(idBill, idTable), BillInfo(idBill, idFood, count), Food(idFood, foodName)
    """
    items = []
    conn = db_connect()
    if not conn:
        return items
    
    try:
        cursor = conn.cursor()
        
        # 1. Tìm idTable từ TableFood
        # Lưu ý: Cột tên bàn là 'tableName', id là 'idTable'
        # Dùng LIKE để bắt trường hợp khách nói "5" nhưng DB lưu "Bàn 5"
        param = f"%{table_name}%"
        cursor.execute("SELECT idTable, tableName FROM TableFood WHERE tableName LIKE ?", (param,))
        row_table = cursor.fetchone()
        
        if row_table:
            table_id = row_table[0]
            # real_table_name = row_table[1] # Dùng để hiển thị cho đẹp nếu cần
            
            # 2. Tìm Bill chưa thanh toán (status = 0)
            # Cột id là 'idBill', cột tham chiếu là 'idTable'
            cursor.execute("SELECT idBill FROM Bill WHERE idTable = ? AND status = 0", (table_id,))
            row_bill = cursor.fetchone()
            
            if row_bill:
                bill_id = row_bill[0]
                
                # 3. Lấy chi tiết món từ BillInfo JOIN Food
                # Các cột cần lấy: f.foodName, bi.count, f.price, f.idFood
                query = """
                SELECT f.foodName, bi.count, f.price, f.idFood
                FROM BillInfo bi
                JOIN Food f ON bi.idFood = f.idFood
                WHERE bi.idBill = ?
                """
                cursor.execute(query, (bill_id,))
                rows = cursor.fetchall()
                
                for r in rows:
                    items.append({
                        "food": r[0],        # foodName
                        "quantity": r[1],    # count
                        "price": float(r[2]),# price
                        "idFood": r[3]       # idFood
                    })
                    
    except Exception as e:
        print(f"Error loading DB order: {e}")
    finally:
        conn.close()
        
    return items

# =================================================================================
# CÁC CLASS ACTIONS CỦA RASA
# =================================================================================
class ActionProcessOrder(Action):
    def name(self) -> Text:
        return "action_process_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        text = tracker.latest_message.get('text', '') or ''
        
        # -------------------------------------------------------------------------
        # 1. XÁC ĐỊNH BÀN (Sử dụng hàm helper mới đã sửa)
        # -------------------------------------------------------------------------
        # Hàm find_table_by_text mới giờ đây sẽ trả về "Mang về" nếu không tìm thấy số bàn
        new_table_info = find_table_by_text(text)           
        current_table_slot = tracker.get_slot('table_name') 
        
        # Logic ưu tiên:
        # 1. Nếu tìm thấy Bàn cụ thể trong câu nói -> Dùng nó.
        # 2. Nếu không, dùng bàn đang nhớ trong slot.
        # 3. Nếu slot cũng trống -> Dùng kết quả "Mang về" từ hàm helper.
        if "Bàn" in new_table_info:
            target_table = new_table_info
        elif current_table_slot:
            target_table = current_table_slot
        else:
            target_table = new_table_info # Lúc này là "Mang về"

        slot_events = []
        # Chỉ update slot nếu thông tin mới khác thông tin cũ
        if target_table != current_table_slot:
            slot_events.append(SlotSet('table_name', target_table))

        # -------------------------------------------------------------------------
        # 2. KHỞI TẠO & LOAD DỮ LIỆU
        # -------------------------------------------------------------------------
        resolved = []        # Giỏ hàng chính thức
        pending_options = [] # Danh sách món chờ chưa rõ 
        
        # A. Lấy những món đang có trong Slot
        items_in_slot = [] 
        old_payload_json = tracker.get_slot("pending_order")
        if old_payload_json:
            try:
                old_payload = json.loads(old_payload_json)
                items_in_slot = old_payload.get("resolved", [])
                pending_options = old_payload.get("pending", [])
            except:
                pass

        # B. Quyết định Load DB
        should_load_db = False
        # Nếu đổi sang một BÀN CỤ THỂ mới, hoặc có bàn mà slot rỗng -> Load DB
        if "Bàn" in target_table and (target_table != current_table_slot or not items_in_slot):
             should_load_db = True

        # C. Thực hiện Load và Merge
        if should_load_db:
            db_items = load_current_order_from_db(target_table)
            resolved = db_items
            
            # Gộp món từ slot cũ sang bàn mới (nếu có chuyển bàn)
            if items_in_slot:
                for item in items_in_slot:
                    merge_items(resolved, item)
        else:
            resolved = items_in_slot

        # -------------------------------------------------------------------------
        # 3. PHÂN TÍCH INTENT & SỐ LƯỢNG
        # -------------------------------------------------------------------------
        is_reduction = check_reduction_keyword(text)
        multiplier = -1 if is_reduction else 1 

        # -------------------------------------------------------------------------
        # 4. TRÍCH XUẤT MÓN (REGEX + ENTITY NÂNG CAO)
        # -------------------------------------------------------------------------
        nummap = { 'một':1,'mot':1,'1':1,'hai':2,'2':2,'ba':3,'3':3,'bốn':4,'4':4,'năm':5,'5':5, 'sáu':6,'6':6, 'bảy':7,'7':7,'tám':8,'8':8,'chín':9,'9':9,'mười':10,'10':10 }
        
        # Regex 1: Số + Tên (VD: "2 gà")
        pattern = re.compile(r"(?:(\d+|một|mot|hai|ba|bốn|nam|năm)\s+([\w\sàáạảãâầấậẩẫăằắặẳẵêềếệểễôơưứừữựếé]+?))(?=\s*(?:,|và|\.|$|ở|tại|bàn|mang|về))", flags=re.IGNORECASE)        
        matches = pattern.findall(text)
        items_current_turn = []
        
        for m in matches:
            qty_token = m[0].lower()
            qty = nummap.get(qty_token, 1) 
            try: qty = int(qty_token)
            except: pass
            food_raw = clean_food_text(m[1].strip())
            items_current_turn.append({'food_raw': food_raw, 'quantity': qty * multiplier})

        # Regex 2: Số + Tên ngắn (VD: "2 coke")
        if not items_current_turn:
            short_pattern = re.compile(r"(\d+)\s+([\w\s]+)", flags=re.IGNORECASE)
            m = short_pattern.search(text)
            if m:
                items_current_turn.append({'food_raw': m.group(2).strip(), 'quantity': int(m.group(1)) * multiplier})

        # Fallback: Entity (Cải tiến)
        # Nếu Regex trượt, kiểm tra xem Rasa có bắt được entity 'food' và 'quantity' không
        if not items_current_turn:
            ents = tracker.latest_message.get('entities', [])
            
            # Tìm số lượng trong entity (nếu có)
            entity_qty = 1
            for e in ents:
                if e.get('entity') == 'quantity':
                    entity_qty = extract_quantity(e.get('value')) # Dùng hàm helper extract_quantity
                    break
            
            # Ghép với món ăn
            for e in ents:
                if e.get('entity') == 'food':
                    items_current_turn.append({'food_raw': e.get('value'), 'quantity': entity_qty * multiplier})

        # -------------------------------------------------------------------------
        # 5. TÌM KIẾM & MERGE
        # -------------------------------------------------------------------------
        for it in items_current_turn:
            options = search_foods(it['food_raw'])
            
            if len(options) == 0:
                if not is_reduction:
                    dispatcher.utter_message(text=f"⚠️ Quán chưa tìm thấy món '{it['food_raw']}'. Bạn gọi tên khác thử xem sao nhé.")
                continue 
                
            elif len(options) == 1:
                opt = options[0]
                new_item = {
                    'food': opt['foodName'], 
                    'quantity': it['quantity'], 
                    'idFood': opt['idFood'], 
                    'price': opt['price']
                }
                merge_items(resolved, new_item)
                
            else:
                pending_options.append({'raw': it['food_raw'], 'quantity': it['quantity'], 'options': options})        

        # -------------------------------------------------------------------------
        # 6. XỬ LÝ DISAMBIGUATION (Món trùng tên)
        # -------------------------------------------------------------------------
        if pending_options:
            po = pending_options[0]
            cards_payload = {
                "type": "food_recommendation",
                "title": f"Có {len(po['options'])} món liên quan đến '{po['raw']}', bạn muốn món nào?",
                "items": []
            }
            text_lines = [cards_payload['title']]
            for idx, opt in enumerate(po['options'], start=1):
                cards_payload["items"].append({
                    "id": idx, "name": opt['foodName'], "price": f"{opt['price']:,.0f}đ", "value_to_send": str(idx) 
                })
                text_lines.append(f"{idx}. {opt['foodName']} - {opt['price']:,.0f}đ")
            
            text_lines.append("Vui lòng chọn hoặc gõ số.")
            dispatcher.utter_message(text='\n'.join(text_lines), json_message=cards_payload)
            
            slot_value = {'resolved': resolved, 'pending': pending_options, 'table': target_table}
            
            # Return và Reset quantity
            return slot_events + [
                SlotSet('pending_order', json.dumps(slot_value)),
                SlotSet('quantity', None) # <--- QUAN TRỌNG: Reset quantity
            ]

        # -------------------------------------------------------------------------
        # 7. TỔNG KẾT & TRẢ VỀ
        # -------------------------------------------------------------------------
        final_table_name = target_table 
        if not tracker.get_slot('table_name'):
             slot_events.append(SlotSet('table_name', final_table_name))

        if resolved:
            order_summary_parts = [f"{r['quantity']} {r['food']}" for r in resolved]
            order_summary = join_natural(order_summary_parts)
            location_phrase = get_location_phrase(final_table_name)

            message_text = f"Dạ, mình chốt lại: {order_summary} {location_phrase}.\n\nBạn có muốn thêm món hay xác nhận luôn không ạ?"
            
            payload = {
                'message': message_text, 'order': resolved, 'table': final_table_name, 'status': 'pending_confirmation'
            }
            dispatcher.utter_message(text=payload['message'], json_message=payload)
            
            return slot_events + [
                SlotSet('pending_order', json.dumps({'resolved': resolved, 'pending': [], 'table': final_table_name}, ensure_ascii=False)),
                SlotSet('quantity', None),     # <--- Reset để tránh bug ám số lượng
                SlotSet('food', None),         # <--- Reset food
                SlotSet('food_to_add', None),  # <--- Reset các slot rác
                SlotSet('food_to_remove', None)
            ]
        else:
            location_phrase = get_location_phrase(final_table_name)
            dispatcher.utter_message(text=f"Dạ, đơn hàng hiện đang trống {location_phrase}. Bạn muốn gọi món gì ạ?")
            
            return slot_events + [
                SlotSet('pending_order', json.dumps({'resolved': [], 'pending': [], 'table': final_table_name}, ensure_ascii=False)),
                SlotSet('quantity', None) # <--- Reset quantity
            ]


class ActionResolveChoice(Action):
    def name(self) -> Text:
        return "action_resolve_choice"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        p = tracker.get_slot('pending_order')
        if not p:
            dispatcher.utter_message(text="Hiện không có lựa chọn nào đang chờ.")
            return []

        try:
            payload = json.loads(p)
            pending_options = payload.get('pending', [])
            resolved_items = payload.get('resolved', [])
            if not pending_options: return []
        except json.JSONDecodeError:
            return [SlotSet('pending_order', None)]

        user_text = tracker.latest_message.get('text', '').lower()
        current_pending_item = pending_options[0]
        options = current_pending_item.get('options', [])
        
        items_to_add = [] 

        # Logic 1: Nhận diện từ giao diện C# gửi lên (món số X số lượng Y)
        specific_matches = re.findall(r"món số\s*(\d+)\s*số lượng\s*(\d+)", user_text)
        if specific_matches:
            for idx_str, qty_str in specific_matches:
                try:
                    choice_idx = int(idx_str) - 1 
                    qty = int(qty_str)
                    if 0 <= choice_idx < len(options) and qty > 0:
                        opt = options[choice_idx]
                        # Tạm lưu vào list thêm
                        existing = next((x for x in items_to_add if x['opt']['idFood'] == opt['idFood']), None)
                        if existing: existing['qty'] += qty
                        else: items_to_add.append({'opt': opt, 'qty': qty})
                except ValueError: pass
        else:
            # Logic 2: Fallback (Chat tay)
            stop_words = ['đi', 'ạ', 'nhé', 'nha', 'với', 'cái', 'món', 'lấy', 'cho', 'mình', 'em', 'anh', 'chị', 'chọn', 'số']
            clean_text = user_text
            for word in stop_words: clean_text = re.sub(rf'\b{word}\b', ' ', clean_text).strip()

            match_nums = re.findall(r'(\d+)', clean_text)
            found_by_num = False
            if match_nums:
                for num_str in match_nums:
                    try:
                        choice_idx = int(num_str) - 1 
                        if 0 <= choice_idx < len(options):
                            opt = options[choice_idx]
                            if not any(x['opt']['idFood'] == opt['idFood'] for x in items_to_add):
                                items_to_add.append({'opt': opt, 'qty': 1})
                                found_by_num = True
                    except ValueError: pass

            if not found_by_num and clean_text:
                choices_map = {opt['foodName'].lower(): opt for opt in options}
                list_choices = list(choices_map.keys())
                best_match = process.extractOne(clean_text, list_choices, scorer=fuzz.token_set_ratio)
                if best_match:
                    match_name, score = best_match
                    if score >= 70:
                        opt = choices_map[match_name]
                        items_to_add.append({'opt': opt, 'qty': 1})

        # XỬ LÝ KẾT QUẢ VÀO GIỎ HÀNG
        if items_to_add:
            for item in items_to_add:
                opt = item['opt']
                qty = item['qty']
                new_item = {
                    'food': opt['foodName'], 
                    'quantity': qty, 
                    'idFood': opt['idFood'], 
                    'price': opt['price']
                }
                # GỌI HÀM GỘP THÔNG MINH
                merge_items(resolved_items, new_item)
            
            # Xóa item đang chờ trong pending
            remaining_pending = pending_options[1:]
            
            payload['resolved'] = resolved_items
            payload['pending'] = remaining_pending
            new_payload_json = json.dumps(payload, ensure_ascii=False)
            
            if remaining_pending:
                next_po = remaining_pending[0]
                cards_payload = {
                    "type": "food_recommendation",
                    "title": f"✅ Đã chọn xong. Tiếp theo, món '{next_po['raw']}' bạn muốn loại nào?",
                    "items": []
                }
                text_lines = [cards_payload['title']]
                for idx, opt in enumerate(next_po['options'], start=1):
                    cards_payload["items"].append({
                        "id": idx, "name": opt['foodName'], "price": f"{opt['price']:,.0f}đ", "value_to_send": str(idx) 
                    })
                    text_lines.append(f"{idx}. {opt['foodName']} - {opt['price']:,.0f}đ")
                dispatcher.utter_message(text='\n'.join(text_lines), json_message=cards_payload)
                return [SlotSet('pending_order', new_payload_json)]
            else:
                order_summary_parts = [f"{r['quantity']} {r['food']}" for r in resolved_items]
                order_summary = join_natural(order_summary_parts)
                current_table = tracker.get_slot('table_name') or payload.get('table') or 'Mang về'
                location_phrase = get_location_phrase(current_table)
                
                payload_msg = {
                    'message': f"Dạ, mình chốt lại: {order_summary} {location_phrase}.\n\nBạn xác nhận lên đơn luôn chứ ạ?",
                    'order': resolved_items, 'table': current_table, 'status': 'pending_confirmation'
                }
                dispatcher.utter_message(text=payload_msg['message'], json_message=payload_msg)
                return [SlotSet('table_name', current_table), SlotSet('pending_order', json.dumps({'resolved': resolved_items, 'pending': [], 'table': current_table}, ensure_ascii=False))]
        else:
            dispatcher.utter_message(text=f"🤔 Xin lỗi, tôi không rõ '{user_text}' là món nào. Bạn vui lòng gõ số hoặc tên món nhé.")
            return []


def normalize_food_name(text: str) -> str:
    """Chuẩn hóa tên món ăn: xóa khoảng trắng thừa, viết thường để so sánh"""
    if not text:
        return ""
    # Xóa ký tự đặc biệt, giữ lại chữ cái và số
    text = re.sub(r'[^\w\s]', '', str(text))
    return text.strip()

def extract_quantity(text: Any) -> int:
    """Chuyển đổi text số lượng (số hoặc chữ) thành số nguyên"""
    if not text:
        return 1
    
    text = str(text).lower().strip()
    # 1. Nếu là số sẵn (ví dụ: 2, "2")
    numbers = re.findall(r'\d+', text)
    if numbers:
        return int(numbers[0])
    
    # 2. Nếu là chữ (tiếng Việt)
    word_to_num = {
        'một': 1, 'hai': 2, 'ba': 3, 'bốn': 4, 'năm': 5,
        'sáu': 6, 'bảy': 7, 'tám': 8, 'chín': 9, 'mười': 10,
        'chục': 10
    }
    for word, num in word_to_num.items():
        if word in text:
            return num
    return 1

def parse_change_command_from_text(text: str):
    """
    Hàm phân tích cú pháp thủ công (Rule-based) để tách vế Bỏ và Thêm.
    Ví dụ: "đổi cơm gà thành phở bò" -> remove="cơm gà", add="phở bò"
    """
    if not text:
        return None, None

    text_lower = text.lower()
    
    # 1. Các từ khóa ngăn cách giữa món cũ và món mới
    separators = [" thành ", " lấy ", " sang ", " bằng ", " qua "]
    
    split_point = -1
    used_sep = ""
    
    # Tìm từ ngăn cách xuất hiện đầu tiên
    for sep in separators:
        idx = text_lower.find(sep)
        if idx != -1:
            split_point = idx
            used_sep = sep
            break
            
    if split_point == -1:
        return None, None # Không tìm thấy cấu trúc "A thành B"

    # 2. Tách chuỗi
    part_remove = text_lower[:split_point]
    part_add = text_lower[split_point + len(used_sep):]

    # 3. Làm sạch phần BỎ (Remove)
    # Các từ khóa hành động cần xóa đi
    remove_keywords = [
        "thay đổi", "thay", "đổi", "bỏ", "không lấy", "xoá", "xóa", 
        "mình muốn", "cho tôi", "cho mình", "anh muốn", "em muốn"
    ]
    for kw in remove_keywords:
        part_remove = part_remove.replace(kw, "")
    
    # 4. Làm sạch phần THÊM (Add)
    # Thường phần thêm ở cuối câu nên ít rác hơn, nhưng cứ clean cơ bản
    add_keywords = ["một", "hai", "ba", "suất", "phần", "tô", "dĩa", "ly", "chén"]
    # (Optional: Có thể xóa định lượng text ở đây nếu muốn strict matching)

    return part_remove.strip(), part_add.strip()


class ActionChangeOrder(Action):
    def name(self) -> Text:
        return "action_change_order"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        # 1. KHỞI TẠO & KIỂM TRA DỮ LIỆU CƠ BẢN
        json_order = tracker.get_slot("pending_order")
        user_msg = tracker.latest_message.get('text', "")
        intent_name = tracker.latest_message['intent'].get('name')
        
        # Lấy tất cả các slot có thể liên quan
        slot_remove = tracker.get_slot("food_to_remove")
        slot_add = tracker.get_slot("food_to_add")
        slot_food_generic = next(tracker.get_latest_entity_values("food"), None)
        
        # [FIX QUAN TRỌNG]: Kiểm tra xem câu hiện tại có SỐ LƯỢNG không?
        current_entities = tracker.latest_message.get('entities', [])
        explicit_qty_val = None
        
        for ent in current_entities:
            if ent['entity'] == 'quantity':
                explicit_qty_val = extract_quantity(ent['value'])
                break
        
        # Nếu không có đơn hàng thì báo lỗi ngay
        if not json_order:
            dispatcher.utter_message(text="❌ Hiện tại chưa có đơn hàng nào để sửa.")
            return [SlotSet("quantity", None)]

        try:
            payload = json.loads(json_order)
            current_items = payload.get("resolved", [])
        except:
            return [SlotSet("quantity", None)]

        if not current_items:
            dispatcher.utter_message(text="❌ Đơn hàng đang trống, không có gì để sửa.")
            return [SlotSet("quantity", None)]

        # 2. XÁC ĐỊNH MỤC TIÊU (TARGET) DỰA TRÊN INTENT
        target_remove = None
        target_add = None
        mode = "unknown" # swap, remove, add, update_qty

        # Parse text thủ công (Hỗ trợ câu phức tạp)
        txt_remove, txt_add = parse_change_command_from_text(user_msg)

        # --- CASE A: SWAP ---
        if intent_name == "change_food":
            mode = "swap"
            target_remove = slot_remove if slot_remove else txt_remove
            target_add = slot_add if slot_add else txt_add

        # --- CASE B: REMOVE ---
        elif intent_name == "remove_item":
            mode = "remove"
            target_remove = slot_remove if slot_remove else slot_food_generic
        
        # --- CASE C: ADD ---
        elif intent_name == "add_item":
            mode = "add"
            target_add = slot_add if slot_add else slot_food_generic

        # --- CASE D: CHANGE QUANTITY ---
        elif intent_name == "change_quantity":
            mode = "update_qty"
            target_remove = slot_food_generic 

        # --- CASE E: FALLBACK ---
        else:
            if slot_remove and slot_add: mode = "swap"; target_remove = slot_remove; target_add = slot_add
            elif slot_remove: mode = "remove"; target_remove = slot_remove
            elif slot_add: mode = "add"; target_add = slot_add
            elif slot_food_generic: 
                 dispatcher.utter_message(text=f"Bạn muốn thêm, bớt hay đổi món '{slot_food_generic}'?")
                 return [SlotSet("quantity", None)]

        # Biến theo dõi kết quả
        updated = False
        messages = []
        errors = []

        # ==========================================================
        # 3. XỬ LÝ LOGIC THEO TỪNG CHẾ ĐỘ (MODE)
        # ==========================================================

        # ----------------------------------------------------------
        # LOGIC 1: SWAP (ĐỔI MÓN) - Logic khó nhất
        # ----------------------------------------------------------
        if mode == "swap" and target_remove and target_add:
            clean_remove = normalize_food_name(target_remove)
            current_names = [item['food'] for item in current_items]
            
            # Tìm món cũ
            match_name, score = process.extractOne(clean_remove, current_names, scorer=fuzz.token_sort_ratio)
            
            item_to_swap = None
            index_to_swap = -1
            
            if match_name and score >= 60:
                for i, item in enumerate(current_items):
                    if item['food'] == match_name:
                        item_to_swap = item
                        index_to_swap = i
                        break
            
            if item_to_swap:
                # [LOGIC KẾ THỪA]: Nếu khách không nói số mới -> Lấy số cũ
                final_qty = explicit_qty_val if explicit_qty_val else item_to_swap['quantity']
                
                # Tìm món mới trong DB
                clean_add = normalize_food_name(target_add)
                conn = pyodbc.connect(CONN_STR)
                cursor = conn.cursor()
                cursor.execute("SELECT idFood as Id, FoodName as Name, Price FROM Food")
                rows = cursor.fetchall()
                db_names = [r.Name for r in rows]
                best_match_add, score_add = process.extractOne(clean_add, db_names, scorer=fuzz.token_sort_ratio)
                
                best_record_add = None
                if score_add >= 60:
                    for r in rows:
                        if r.Name == best_match_add:
                            best_record_add = r
                            break
                conn.close()

                if best_record_add:
                    # Xóa món cũ
                    removed_item = current_items.pop(index_to_swap)
                    
                    # Thêm món mới
                    new_item = {
                        "food": best_record_add.Name,
                        "quantity": final_qty,
                        "idFood": best_record_add.Id,
                        "price": float(best_record_add.Price)
                    }
                    merge_items(current_items, new_item)
                    messages.append(f"đổi {removed_item['food']} thành {final_qty} {best_record_add.Name}")
                    updated = True
                else:
                    errors.append(f"quán không có món '{target_add}'")
            else:
                errors.append(f"không tìm thấy món '{target_remove}' trong đơn")

        # ----------------------------------------------------------
        # LOGIC 2: REMOVE / UPDATE QTY
        # ----------------------------------------------------------
        elif mode in ["remove", "update_qty"] and target_remove:
            # Mặc định là 1 nếu không nói số lượng
            qty_val = explicit_qty_val if explicit_qty_val else 1 
            
            clean_name = normalize_food_name(target_remove)
            current_names = [item['food'] for item in current_items]
            match_name, score = process.extractOne(clean_name, current_names, scorer=fuzz.token_sort_ratio)
            
            if match_name and score >= 60:
                for i, item in enumerate(current_items):
                    if item['food'] == match_name:
                        if mode == "remove":
                            # Chỉ trừ số lượng nếu khách NÓI RÕ con số
                            if explicit_qty_val: 
                                item['quantity'] -= qty_val
                                if item['quantity'] <= 0:
                                    current_items.pop(i)
                                    messages.append(f"xóa bỏ {match_name}")
                                else:
                                    messages.append(f"giảm {qty_val} {match_name}")
                            else: 
                                # Không nói số -> Xóa hết
                                current_items.pop(i)
                                messages.append(f"xóa bỏ {match_name}")
                            updated = True
                        
                        elif mode == "update_qty":
                            if explicit_qty_val:
                                item['quantity'] = qty_val
                                messages.append(f"đổi số lượng {match_name} thành {qty_val}")
                                updated = True
                            else:
                                dispatcher.utter_message(text=f"Bạn muốn đổi '{match_name}' thành số lượng bao nhiêu?")
                                return [SlotSet("quantity", None)]
                        break
            else:
                errors.append(f"không tìm thấy món '{target_remove}'")

        # ----------------------------------------------------------
        # LOGIC 3: ADD (THÊM MÓN)
        # ----------------------------------------------------------
        elif mode == "add" and target_add:
            qty_val = explicit_qty_val if explicit_qty_val else 1
            
            clean_add = normalize_food_name(target_add)
            conn = pyodbc.connect(CONN_STR)
            cursor = conn.cursor()
            cursor.execute("SELECT idFood as Id, FoodName as Name, Price FROM Food") 
            rows = cursor.fetchall()
            db_names = [r.Name for r in rows]
            
            best_match, score = process.extractOne(clean_add, db_names, scorer=fuzz.token_sort_ratio)
            
            best_record = None
            if score >= 60:
                for r in rows:
                    if r.Name == best_match:
                        best_record = r
                        break
            conn.close()

            if best_record:
                new_item = {
                    "food": best_record.Name,
                    "quantity": qty_val,
                    "idFood": best_record.Id,
                    "price": float(best_record.Price)
                }
                merge_items(current_items, new_item)
                messages.append(f"thêm {qty_val} {best_record.Name}")
                updated = True
            else:
                errors.append(f"quán không có món '{target_add}'")

        # ==========================================================
        # 4. TẠO PHẢN HỒI & TRẢ VỀ KẾT QUẢ
        # ==========================================================
        
        payload['resolved'] = current_items
        final_names = [f"{item['quantity']} {item['food']}" for item in current_items]
        summary_order = join_natural(final_names) if final_names else "Trống"

        response_text = ""
        
        if errors:
            err_str = join_natural(errors)
            response_text += f"⚠️ Có chút vấn đề: {err_str}. "

        if updated:
            msg_str = join_natural(messages)
            msg_str = msg_str[0].upper() + msg_str[1:] if msg_str else ""
            response_text += f"✅ {msg_str}. "
            response_text += f"\n👉 Đơn hiện tại: {summary_order}."
            
            dispatcher.utter_message(text=response_text)
            
            return [
                SlotSet("pending_order", json.dumps(payload, ensure_ascii=False)),
                # Reset sạch các slot tạm
                SlotSet("food_to_remove", None),
                SlotSet("food_to_add", None),
                SlotSet("quantity", None)
            ]
        else:
            if not response_text:
                response_text = "Mình chưa hiểu rõ yêu cầu sửa đổi của bạn. Bạn nói lại giúp mình nhé?"
            
            dispatcher.utter_message(text=response_text)
            return [
                SlotSet("food_to_remove", None),
                SlotSet("food_to_add", None),
                SlotSet("quantity", None)
            ]


class ActionConfirmOrder(Action):
    def name(self) -> Text:
        return "action_confirm_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        pending_json = tracker.get_slot('pending_order')
        if not pending_json: 
            return []
        
        payload = json.loads(pending_json)
        resolved = payload.get('resolved', [])
        table_name = payload.get('table', '')

        if not resolved:
            dispatcher.utter_message(text="Đơn hàng trống trơn à, bạn chọn món lại nhé.")
            return []

        conn = db_connect()
        if not conn:
            dispatcher.utter_message(text="Lỗi kết nối hệ thống nhà bếp.")
            return []

        try:
            cursor = conn.cursor()
            
            # 1. Tìm idTable
            cursor.execute("SELECT idTable FROM TableFood WHERE tableName LIKE ?", (f"%{table_name}%",))
            row_table = cursor.fetchone()
            if not row_table:
                dispatcher.utter_message(text=f"Không tìm thấy bàn {table_name} trong hệ thống.")
                return []
            id_table = row_table[0]

            # 2. Tìm hoặc Tạo Bill
            cursor.execute("SELECT idBill FROM Bill WHERE idTable = ? AND status = 0", (id_table,))
            row_bill = cursor.fetchone()
            
            id_bill = None
            if row_bill:
                id_bill = row_bill[0]
            else:
                cursor.execute("INSERT INTO Bill (DateCheckIn, idTable, status, createdBy) OUTPUT INSERTED.idBill VALUES (GETDATE(), ?, 0, 'admin')", (id_table,))
                id_bill = cursor.fetchone()[0]
                conn.commit()

            # 3. Cập nhật BillInfo & Tạo danh sách hiển thị
            final_items_text = [] # Danh sách để in ra màn hình
            
            for item in resolved:
                id_food = item['idFood']
                qty_final = item['quantity']
                food_name = item['food']
                
                # Check DB
                check_q = "SELECT count FROM BillInfo WHERE idBill = ? AND idFood = ?"
                cursor.execute(check_q, (id_bill, id_food))
                row_detail = cursor.fetchone()
                
                if row_detail:
                    if qty_final <= 0:
                        cursor.execute("DELETE FROM BillInfo WHERE idBill = ? AND idFood = ?", (id_bill, id_food))
                    else:
                        cursor.execute("UPDATE BillInfo SET count = ? WHERE idBill = ? AND idFood = ?", (qty_final, id_bill, id_food))
                else:
                    if qty_final > 0:
                        cursor.execute("INSERT INTO BillInfo (idBill, idFood, count) VALUES (?, ?, ?)", (id_bill, id_food, qty_final))
                
                # Chỉ thêm vào danh sách hiển thị những món có số lượng > 0
                if qty_final > 0:
                    final_items_text.append(f"{qty_final} {food_name}")

            conn.commit()
            
            # 4. TẠO CÂU THÔNG BÁO CHI TIẾT
            if final_items_text:
                # Sử dụng hàm join_natural đã có ở đầu file actions.py
                summary_str = join_natural(final_items_text)
                msg = f"✅ Đã chốt đơn thành công cho {table_name}!\n📋 Thực đơn gồm: {summary_str}.\n\nBếp đang làm ngay ạ!"
            else:
                msg = f"✅ Đã cập nhật xong cho {table_name}. Hiện tại bàn chưa có món nào."

            dispatcher.utter_message(text=msg)
            
            return [SlotSet('pending_order', None)]

        except Exception as e:
            dispatcher.utter_message(text=f"Có lỗi khi lưu đơn: {e}")
            print(e)
        finally:
            conn.close()
        
        return []

class ActionListFoodOptions(Action):
    def name(self) -> Text:
        return "action_list_food_options"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        p = tracker.get_slot('pending_order')
        if not p:
            dispatcher.utter_message(text="Hiện không có danh sách chọn món nào.")
            return []
        
        payload = json.loads(p)
        if payload.get('pending'):
            po = payload['pending'][0]
            cards_payload = {
                "type": "food_recommendation",
                "title": f"Có {len(po['options'])} món liên quan đến '{po['raw']}', bạn muốn món nào?",
                "items": []
            }
            text_lines = [cards_payload['title']]
            for idx, opt in enumerate(po['options'], start=1):
                cards_payload["items"].append({
                    "id": idx, "name": opt['foodName'], "price": f"{opt['price']:,.0f}đ", "value_to_send": str(idx)
                })
                text_lines.append(f"{idx}. {opt['foodName']} - {opt['price']:,.0f}đ")
            dispatcher.utter_message(text='\n'.join(text_lines), json_message=cards_payload)
        else:
            dispatcher.utter_message(text="Không có lựa chọn nào đang chờ.")
        return []

class ActionCancelOrder(Action):
    def name(self) -> Text:
        return "action_cancel_order"
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="👌 Đã huỷ đơn hàng vừa rồi. Khi nào cần gọi món bạn cứ bảo mình nhé!")
        return [SlotSet('pending_order', None), SlotSet('table_name', None)]

