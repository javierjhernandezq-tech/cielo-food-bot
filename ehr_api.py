import json
from flask import Blueprint, request, jsonify

# Nota: Asumiendo que has importado 'get_db_connection' de tu database.py
import psycopg2.extras
from database import get_db_connection
from ai_engine import get_clinical_second_opinion_sync

# Crear el Blueprint para modularizar las rutas. 
# En tu main.py o webhook.py debes registrarlo con: app.register_blueprint(ehr_bp)
ehr_bp = Blueprint('ehr_api', __name__)

@ehr_bp.route('/api/patients/<int:contact_id>/ehr', methods=['GET'])
def get_patient_ehr(contact_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 1. Buscar el perfil del paciente
    cur.execute("SELECT * FROM patient_profiles WHERE contact_id = %s", (contact_id,))
    patient = cur.fetchone()
    
    # Si no existe (es la primera vez que se consulta su historia), se crea vacío automáticamente
    if not patient:
        cur.execute("""
            INSERT INTO patient_profiles (contact_id, odontogram_state) 
            VALUES (%s, '{}'::jsonb) RETURNING *
        """, (contact_id,))
        patient = cur.fetchone()
        conn.commit()

    patient_id = patient['id']
    
    # 2. Obtener historial cronológico de evoluciones médicas
    cur.execute("""
        SELECT id, visit_date, diagnosis, treatment_performed, ai_second_opinion 
        FROM clinical_evolutions 
        WHERE patient_id = %s 
        ORDER BY visit_date DESC
    """, (patient_id,))
    evolutions = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return jsonify({
        "status": "success",
        "profile": patient,
        "evolutions": evolutions
    }), 200


@ehr_bp.route('/api/patients/<int:contact_id>/odontogram', methods=['PUT'])
def update_odontogram(contact_id):
    """Actualiza de forma silenciosa e inmediata el JSONB del odontograma"""
    data = request.get_json()
    odontogram_data = data.get('odontogram_state', {})
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("""
        UPDATE patient_profiles 
        SET odontogram_state = %s 
        WHERE contact_id = %s RETURNING id
    """, (json.dumps(odontogram_data), contact_id))
    
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    
    if result:
        return jsonify({"status": "success", "message": "Odontograma guardado exitosamente"}), 200
    return jsonify({"status": "error", "message": "Paciente no encontrado"}), 404


@ehr_bp.route('/api/patients/<int:contact_id>/evolutions', methods=['POST'])
def add_evolution(contact_id):
    """Guarda la evolución clínica y llama a la Auditoría Médica de Gemini"""
    data = request.get_json()
    evolution_text = data.get('treatment_performed', '')
    diagnosis = data.get('diagnosis', '')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Obtener datos del paciente (alergias y odontograma) para darle contexto a la IA
    cur.execute("SELECT id, allergies, odontogram_state FROM patient_profiles WHERE contact_id = %s", (contact_id,))
    patient = cur.fetchone()
    
    if not patient:
        return jsonify({"status": "error", "message": "Perfil de paciente inexistente"}), 404
        
    patient_id = patient['id']
    allergies = patient.get('allergies', '')
    odontogram_state = patient.get('odontogram_state', {})
    
    # === MAGIA IA ===
    # El servidor invoca la función sincrónica a Gemini enviándole el Odontograma y las Alergias
    ai_response_text = get_clinical_second_opinion_sync(
        evolucion_text=evolution_text, 
        odontogram_data=json.dumps(odontogram_state), 
        patient_allergies=allergies
    )
    
    # Guardar la visita en PostgreSQL
    cur.execute("""
        INSERT INTO clinical_evolutions 
        (patient_id, diagnosis, treatment_performed, ai_second_opinion)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (patient_id, diagnosis, evolution_text, ai_response_text))
    
    evolution_id = cur.fetchone()['id']
    conn.commit()
    cur.close()
    conn.close()
    
    # Parsear el string JSON retornado por Gemini para servirlo de forma estructurada al frontend
    try:
        ai_json = json.loads(ai_response_text)
    except:
        ai_json = {
            "status": "UNKNOWN", 
            "alert_message": "Respuesta no estructurada de la IA.", 
            "suggestions": ai_response_text
        }
    
    return jsonify({
        "status": "success", 
        "evolution_id": evolution_id,
        "ai_analysis": ai_json
    }), 201
