import psycopg2
import psycopg2.extras

def __refresh_ui():
    try:
        from socket_ext import trigger_dashboard_sync
        trigger_dashboard_sync()
    except Exception:
        pass
import os

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("FATAL: DATABASE_URL no está configurada en las variables de entorno.")
    
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Tabla de Contactos (Leads)
        c.execute('''
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                remote_id TEXT UNIQUE NOT NULL,
                name TEXT,
                platform TEXT NOT NULL,
                role TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabla de Mensajes (Historial de Conversación)
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                contact_id INTEGER NOT NULL REFERENCES contacts(id),
                sender_type TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabla de Sesiones (Estado y Handover Humano)
        c.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                contact_id INTEGER PRIMARY KEY REFERENCES contacts(id),
                state TEXT DEFAULT 'IDLE',
                human_active BOOLEAN DEFAULT FALSE,
                additional_data TEXT,
                funnel_stage TEXT DEFAULT 'nuevo'
            )
        ''')

        # 1. patient_profiles (Extensión de contacts para datos demográficos y médicos)
        c.execute('''
            CREATE TABLE IF NOT EXISTS patient_profiles (
                id SERIAL PRIMARY KEY,
                contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                document_id VARCHAR(50) UNIQUE,
                birth_date DATE,
                gender VARCHAR(20),
                systemic_diseases TEXT,
                allergies TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 2. odontogram_state (Como JSONB en patient_profiles)
        c.execute("ALTER TABLE patient_profiles ADD COLUMN IF NOT EXISTS odontogram_state JSONB DEFAULT '{}'::jsonb")

        # 3. clinical_evolutions (Registros de visitas)
        c.execute('''
            CREATE TABLE IF NOT EXISTS clinical_evolutions (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER REFERENCES patient_profiles(id) ON DELETE CASCADE,
                doctor_name VARCHAR(100),
                visit_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                diagnosis TEXT,
                treatment_performed TEXT,
                ai_second_opinion TEXT
            )
        ''')

        # 4. prescriptions (Recetas generadas)
        c.execute('''
            CREATE TABLE IF NOT EXISTS prescriptions (
                id SERIAL PRIMARY KEY,
                evolution_id INTEGER REFERENCES clinical_evolutions(id) ON DELETE CASCADE,
                patient_id INTEGER REFERENCES patient_profiles(id),
                medications JSONB NOT NULL,
                issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 5. documents (Gestor Documental y Radiografías)
        c.execute('''
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER REFERENCES patient_profiles(id) ON DELETE CASCADE,
                file_name VARCHAR(255),
                file_type VARCHAR(50),
                file_url TEXT NOT NULL,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Migración segura en caso de que la tabla ya exista (para no perder data)
        c.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS funnel_stage TEXT DEFAULT 'nuevo'")

        c.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS gcalendar_id TEXT")
            
        # Tabla de Citas Inteligentes
        c.execute('''
            CREATE TABLE IF NOT EXISTS appointments (
                id SERIAL PRIMARY KEY,
                contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                appointment_date TIMESTAMP NOT NULL,
                reason TEXT,
                status TEXT DEFAULT 'agendada',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        __refresh_ui()
        c.close()
        conn.close()
    except Exception as e:
        print("Error inicializando DataBase. Por favor verifica tu DATABASE_URL.")
        print(e)

# Helpers
def get_or_create_contact(remote_id: str, platform: str, name: str = "Desconocido"):
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        c.execute("SELECT id, name, role FROM contacts WHERE remote_id = %s", (remote_id,))
        row = c.fetchone()
        
        if not row:
            c.execute("INSERT INTO contacts (remote_id, name, platform) VALUES (%s, %s, %s) RETURNING id", (remote_id, name, platform))
            contact_id = c.fetchone()['id']
            c.execute("INSERT INTO sessions (contact_id) VALUES (%s)", (contact_id,))
            conn.commit()
            __refresh_ui()
            return {"id": contact_id, "name": name, "role": None}
        
        return dict(row)
    finally:
        conn.close()

def update_contact_role(contact_id: int, role: str):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("UPDATE contacts SET role = %s WHERE id = %s", (role, contact_id))
        conn.commit()
        __refresh_ui()
    finally:
        conn.close()

def update_contact_profile(contact_id: int, new_name: str, new_phone: str = None):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        if new_phone:
            c.execute("UPDATE contacts SET name = %s, remote_id = %s WHERE id = %s", (new_name, new_phone, contact_id))
        else:
            c.execute("UPDATE contacts SET name = %s WHERE id = %s", (new_name, contact_id))
        conn.commit()
        __refresh_ui()
    finally:
        conn.close()

def create_manual_contact(name: str, phone: str) -> int:
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id FROM contacts WHERE remote_id = %s", (phone,))
        existing = c.fetchone()
        if existing:
            return existing['id']
            
        c.execute("INSERT INTO contacts (remote_id, name, platform) VALUES (%s, %s, 'whatsapp') RETURNING id", (phone, name))
        contact_id = c.fetchone()['id']
        c.execute("INSERT INTO sessions (contact_id, human_active) VALUES (%s, TRUE)", (contact_id,))
        try:
            c.execute("UPDATE sessions SET funnel_stage = 'nuevo' WHERE contact_id = %s", (contact_id,))
        except Exception:
            pass # Si la columna funnel_stage no existe por base de datos vieja, ignóralo
        conn.commit()
        __refresh_ui()
        return contact_id
    except Exception as e:
        print("Error en create_manual_contact:", e)
        conn.rollback()
        raise e
    finally:
        conn.close()

def log_message(contact_id: int, sender_type: str, content: str):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO messages (contact_id, sender_type, content) VALUES (%s, %s, %s)", 
                     (contact_id, sender_type, content))
        conn.commit()
        __refresh_ui()
    finally:
        conn.close()

def get_session_state(contact_id: int) -> dict:
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT state, human_active, additional_data, funnel_stage FROM sessions WHERE contact_id = %s", (contact_id,))
        row = c.fetchone()
        if row:
            return dict(row)
        return {"state": "IDLE", "human_active": False, "additional_data": "{}", "funnel_stage": "nuevo"}
    finally:
        conn.close()

def update_session_state(contact_id: int, state: str, human_active: bool = None, additional_data: str = None, funnel_stage: str = None):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        query = "UPDATE sessions SET state = %s"
        params = [state]
        if human_active is not None:
            query += ", human_active = %s"
            params.append(human_active)
        if additional_data is not None:
            query += ", additional_data = %s"
            params.append(additional_data)
        if funnel_stage is not None:
            query += ", funnel_stage = %s"
            params.append(funnel_stage)
        
        query += " WHERE contact_id = %s"
        params.append(contact_id)
        
        c.execute(query, tuple(params))
        conn.commit()
        __refresh_ui()
    finally:
        conn.close()

# Si existe URL en el entorno, inicializamos. Fallará si no existe y lo avisa.
if DATABASE_URL:
    init_db()

def delete_message_db(message_id: int):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM messages WHERE id = %s", (message_id,))
        conn.commit()
        __refresh_ui()
    finally:
        conn.close()

# ============================================================
# API DE CITAS (CALENDARIO)
# ============================================================

def _sync_create_event_bg(contact_id, date_str, time_str, reason, sys_appointment_id):
    conn = get_db_connection()
    if not conn: return
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT name FROM contacts WHERE id = %s", (contact_id,))
        contact = c.fetchone()
        contact_name = contact['name'] if contact else "Paciente Desconocido"
        
        from gcalendar_sync import create_google_event
        event_id = create_google_event(contact_name, date_str, time_str, reason)
        
        if event_id:
            c.execute("UPDATE appointments SET gcalendar_id = %s WHERE id = %s", (event_id, sys_appointment_id))
            conn.commit()
    except Exception as e:
        print(f"Error en bg gcalendar sync: {e}")
    finally:
        conn.close()

def create_appointment(contact_id: int, date_str: str, time_str: str, reason: str):
    """
    Crea una cita en la BBDD y sincroniza con Google Calendar.
    """
    conn = get_db_connection()
    try:
        dt_str = f"{date_str} {time_str}:00"
        
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id FROM appointments WHERE appointment_date = %s AND status != 'cancelada'", (dt_str,))
        if c.fetchone():
            raise ValueError("Horario ocupado")
            
        c.execute(
            "INSERT INTO appointments (contact_id, appointment_date, reason) VALUES (%s, %s, %s) RETURNING id",
            (contact_id, dt_str, reason)
        )
        new_id = c.fetchone()['id']
        conn.commit()
        
        import threading
        threading.Thread(target=_sync_create_event_bg, args=(contact_id, date_str, time_str, reason, new_id), daemon=True).start()
        
        __refresh_ui()
    except ValueError:
        conn.rollback()
        raise
    except Exception as e:
        print(f"[DB ERROR] create_appointment: {e}")
        conn.rollback()
    finally:
        conn.close()

def delete_contact_db(contact_id: int):
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM messages WHERE contact_id = %s", (contact_id,))
        c.execute("DELETE FROM sessions WHERE contact_id = %s", (contact_id,))
        c.execute("DELETE FROM contacts WHERE id = %s", (contact_id,))
        conn.commit()
        __refresh_ui()
    finally:
        conn.close()

def get_active_appointments_for_contact(contact_id: int) -> list:
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("""
            SELECT id, to_char(appointment_date, 'YYYY-MM-DD') as date_str, 
                   to_char(appointment_date, 'HH24:MI') as time_str, reason, status
            FROM appointments 
            WHERE contact_id = %s AND status != 'cancelada'
            ORDER BY appointment_date ASC
        """, (contact_id,))
        return [dict(row) for row in c.fetchall()]
    except Exception as e:
        print(f"[DB ERROR] get_appointments: {e}")
        return []
    finally:
        conn.close()

def get_all_upcoming_appointments() -> list:
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("""
            SELECT to_char(appointment_date, 'YYYY-MM-DD') as date_str, 
                   to_char(appointment_date, 'HH24:MI') as time_str
            FROM appointments 
            WHERE status != 'cancelada' AND appointment_date >= CURRENT_DATE
            ORDER BY appointment_date ASC
        """)
        return [dict(row) for row in c.fetchall()]
    except Exception as e:
        print(f"[DB ERROR] get_all_upcoming_appointments: {e}")
        return []
    finally:
        conn.close()

def _sync_delete_event_bg(gcalendar_id):
    if gcalendar_id:
        from gcalendar_sync import delete_google_event
        delete_google_event(gcalendar_id)

def cancel_appointment(appt_id: int, contact_id_verify: int = None):
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT gcalendar_id FROM appointments WHERE id = %s", (appt_id,))
        row = c.fetchone()
        
        if contact_id_verify is not None:
             c.execute("UPDATE appointments SET status = 'cancelada' WHERE id = %s AND contact_id = %s", (appt_id, contact_id_verify))
        else:
             c.execute("UPDATE appointments SET status = 'cancelada' WHERE id = %s", (appt_id,))
        conn.commit()
        
        if row and row['gcalendar_id']:
            import threading
            threading.Thread(target=_sync_delete_event_bg, args=(row['gcalendar_id'],), daemon=True).start()
            
        __refresh_ui()
    except Exception as e:
        print(f"[DB ERROR] cancel_appointment: {e}")
        conn.rollback()
    finally:
        conn.close()

def _sync_reschedule_event_bg(appt_id, old_gcal_id, contact_id, date_str, time_str, reason):
    if old_gcal_id:
        from gcalendar_sync import delete_google_event
        delete_google_event(old_gcal_id)
    _sync_create_event_bg(contact_id, date_str, time_str, reason, appt_id)

def reschedule_appointment(appt_id: int, new_date_str: str, new_time_str: str, contact_id_verify: int = None):
    conn = get_db_connection()
    try:
        dt_str = f"{new_date_str} {new_time_str}:00"
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        c.execute("SELECT contact_id, reason, gcalendar_id FROM appointments WHERE id = %s", (appt_id,))
        row_appt = c.fetchone()
        
        c.execute("SELECT id FROM appointments WHERE appointment_date = %s AND id != %s AND status != 'cancelada'", (dt_str, appt_id))
        if c.fetchone():
            raise ValueError("Horario ocupado")

        if contact_id_verify is not None:
            c.execute("UPDATE appointments SET appointment_date = %s WHERE id = %s AND contact_id = %s", (dt_str, appt_id, contact_id_verify))
        else:
            c.execute("UPDATE appointments SET appointment_date = %s WHERE id = %s", (dt_str, appt_id))
        conn.commit()
        
        if row_appt:
            import threading
            threading.Thread(target=_sync_reschedule_event_bg, 
                             args=(appt_id, row_appt['gcalendar_id'], row_appt['contact_id'], new_date_str, new_time_str, row_appt['reason']), 
                             daemon=True).start()
                             
        __refresh_ui()
    except ValueError:
        conn.rollback()
        raise
    except Exception as e:
        print(f"[DB ERROR] reschedule_appointment: {e}")
        conn.rollback()
    finally:
        conn.close()

def hard_delete_appointment(appt_id: int):
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT gcalendar_id FROM appointments WHERE id = %s", (appt_id,))
        row = c.fetchone()
        
        c.execute("DELETE FROM appointments WHERE id = %s", (appt_id,))
        conn.commit()
        
        if row and row['gcalendar_id']:
            import threading
            threading.Thread(target=_sync_delete_event_bg, args=(row['gcalendar_id'],), daemon=True).start()
            
        __refresh_ui()
    except Exception as e:
        print(f"[DB ERROR] hard_delete: {e}")
    finally:
        conn.close()

def get_recent_messages(contact_id: int, limit: int = 5) -> list:
    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("""
            SELECT sender_type, content 
            FROM (
                SELECT sender_type, content, timestamp 
                FROM messages 
                WHERE contact_id = %s 
                ORDER BY timestamp DESC 
                LIMIT %s
            ) sub
            ORDER BY timestamp ASC
        """, (contact_id, limit))
        return [dict(row) for row in c.fetchall()]
    except Exception as e:
        print(f"[DB ERROR] get_recent_messages: {e}")
        return []
    finally:
        conn.close()
