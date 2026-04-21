import os
import httpx
import sqlite3
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import uuid

load_dotenv()

app = FastAPI(title="Cielo Food House - WhatsApp Bot & Kanban MVP")

# Habilitar CORS para permitir fetch interactivo si hace falta
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "cielo_secret_token")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "").replace("Bearer ", "").strip()
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

# --- INICIALIZACIÓN DE BASE DE DATOS (SQLite) ---
DB_FILE = "cielo.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cielo_orders (
            id TEXT PRIMARY KEY,
            item TEXT NOT NULL,
            total REAL NOT NULL,
            method TEXT NOT NULL,
            phone TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'recibido'
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- MEMORIA RAM SIMULADA PARA CARRITOS ---
USER_STATES = {}
USER_CARTS = {}

MENU = {
    "1": {"name": "Mix Fiestero", "price": 38000},
    "2": {"name": "Docena de Tequeños", "price": 20000},
    "3": {"name": "25 Tequeños de Fiesta", "price": 21000},
    "4": {"name": "Empanadas", "price": 3000},
    "5": {"name": "Pasteles Andinos", "price": 2000},
    "6": {"name": "Mandocas", "price": 2000}
}

PAYMENT_METHODS = {
    "1": "Mercado Pago",
    "2": "Efectivo",
    "3": "Transferencia Bancaria"
}

def format_menu():
    text = "☁️ *CIELO FOOD HOUSE - MENÚ* ☁️\n"
    text += "Momentos que saben a Venezuela 🇻🇪\n\n"
    for k, v in MENU.items():
        text += f"*{k}.* {v['name']} - ${v['price']}\n"
    text += "\n📍 Escribe el número del producto que deseas pedir:"
    return text

def calculate_total(cart):
    return sum(item["price"] * item["qty"] for item in cart)

def format_cart(cart):
    text = "🛒 *TU CARRITO:*\n"
    for item in cart:
        text += f"- {item['qty']}x {item['name']} = ${item['price'] * item['qty']}\n"
    text += f"\n*TOTAL:* ${calculate_total(cart)}\n"
    return text

def save_order_to_db(items_summary: str, total_price: float, method: str, phone: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Generar un ID corto pero único referenciando a Cielo
    new_id = "CL-" + str(uuid.uuid4().hex[:5]).upper()
    cursor.execute('''
        INSERT INTO cielo_orders (id, item, total, method, phone, state)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (new_id, items_summary, total_price, method, phone, "recibido"))
    conn.commit()
    conn.close()

def process_whatsapp_state(sender_id: str, message_text: str):
    """Máquina de Estados Conversacional (Bot)"""
    
    current_state = USER_STATES.get(sender_id, "START")
    text_input = message_text.strip().lower()
    
    if current_state == "START":
        USER_STATES[sender_id] = "PICKING"
        USER_CARTS[sender_id] = []
        return f"¡Hola! Bienvenido a *Cielo Food House* 🎉.\n\n{format_menu()}"
        
    elif current_state == "PICKING":
        if text_input in MENU:
            item = MENU[text_input]
            USER_STATES[sender_id] = "QUANTITY"
            USER_STATES[f"{sender_id}_temp_item"] = item
            return f"Seleccionaste *{item['name']}*.\n¿Cuántas cantidades (porciones/unidades) deseas? Escribe en números (ej: 2)."
        else:
            return "Opción inválida. 🤔 Por favor, escríbeme el **número** del menú que quieres (ej: 1, 2, 3)."

    elif current_state == "QUANTITY":
        if not text_input.isdigit() or int(text_input) <= 0:
            return "Por favor ingresa un número válido mayor a cero."
            
        qty = int(text_input)
        item = USER_STATES.get(f"{sender_id}_temp_item")
        
        cart = USER_CARTS.get(sender_id, [])
        cart.append({"name": item["name"], "price": item["price"], "qty": qty})
        USER_CARTS[sender_id] = cart
        
        USER_STATES[sender_id] = "MORE_ITEMS"
        
        msg = f"✅ ¡Agregado! 🤤\n\n{format_cart(cart)}\n"
        msg += "¿Deseas agregar algo más o proceder al pago?\n"
        msg += "Escribe *1* para PAGAR\n"
        msg += "Escribe *2* para AGREGAR MÁS PRODUCTOS"
        return msg

    elif current_state == "MORE_ITEMS":
        if text_input == "2":
            USER_STATES[sender_id] = "PICKING"
            return format_menu()
        elif text_input == "1":
            USER_STATES[sender_id] = "PAYMENT_METHOD"
            msg = "Excelente. 💳 ¿Qué método de pago prefieres?\n\n"
            for k, v in PAYMENT_METHODS.items():
                msg += f"*{k}.* {v}\n"
            msg += "\nResponder con el número."
            return msg
        else:
            return "Opción inválida. Escribe 1 para pagar o 2 para agregar más cosas."

    elif current_state == "PAYMENT_METHOD":
        if text_input in PAYMENT_METHODS:
            method_name = PAYMENT_METHODS[text_input]
            USER_STATES[f"{sender_id}_payment"] = method_name
            
            if text_input == "2": # Efectivo
                USER_STATES[sender_id] = "AWAITING_ADDRESS"
                return "Has seleccionado *Efectivo*. 💵 Por favor, envíanos la dirección de envío exacta o si prefieres pasar a retirar (Take Away)."
            else: # Mercado Pago / Transferencia
                USER_STATES[sender_id] = "AWAITING_RECEIPT"
                if text_input == "1":
                    return "Alias Mercado Pago: cielofood\nCBU: 00000031200000000000\n\nTransferí y envíame la CAPTURA del comprobante por aquí y tu Dirección de envío."
                else: 
                    return "CBU Cuenta Galicia: 00000031200000000000\nAlias: cielofood\n\nTransferí y envíame la CAPTURA del comprobante por aquí y tu Dirección de envío."
        else:
            return "Opción inválida. Elige un número del 1 al 3."

    elif current_state in ["AWAITING_ADDRESS", "AWAITING_RECEIPT"]:
        cart = USER_CARTS.get(sender_id, [])
        total = calculate_total(cart)
        method = USER_STATES.get(f"{sender_id}_payment")
        
        # Generar un resumen de qué se compró en texto
        items_summary = ", ".join([f"{i['qty']}x {i['name']}" for i in cart])
        if len(items_summary) == 0: items_summary = "Orden vacía"
        
        # Inserción en Base de Datos Real SQLite!
        save_order_to_db(items_summary, total, method, sender_id)
        
        USER_STATES[sender_id] = "START"
        USER_CARTS[sender_id] = []
        
        return "¡Pedido Confirmado! ✅ Tu orden ha entrado directamente en la pantalla de nuestra cocina. Pronto nos pondremos en contacto contigo si falta algún detalle. ¡Gracias por elegir el buen sabor!"

    # Fallback
    USER_STATES[sender_id] = "START"
    return "Ups, me perdí. Vamos de nuevo... Escribe cualquier cosa para empezar."


# --- FUNCION ALERTA WHATSAPP (BOTON) ---
async def send_whatsapp_message(to_number: str, text: str):
    """Envia mensaje HTTP a Graph API de Meta."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print(f"[NO KEYS EN RAILWAY] Simulando envío a {to_number}: {text}")
        return

    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"preview_url": False, "body": text}
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                print(f"GraphAPI Error: {r.text}")
        except Exception as e:
            print(f"Error HTTP enviando a GraphAPI: {e}")

# --- WEBHOOK ENDPOINTS ---
@app.get("/api/whatsapp/webhook")
async def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_challenge = request.query_params.get("hub.challenge")
    hub_verify_token = request.query_params.get("hub.verify_token")

    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(str(hub_challenge))
    raise HTTPException(status_code=403, detail="Token no válido")

@app.post("/api/whatsapp/webhook")
async def receive_webhook(request: Request):
    data = await request.json()
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if messages:
            message = messages[0]
            sender_id = message.get("from")
            msg_text = message.get("text", {}).get("body", "IMAGEN/DOCUMENTO")
            
            response_text = process_whatsapp_state(sender_id, msg_text)
            await send_whatsapp_message(sender_id, response_text)
            
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return {"status": "success"}

# --- KANBAN FRONTEND + API REST ENDPOINTS ---

class StateUpdate(BaseModel):
    state: str

@app.get("/api/orders")
def get_orders():
    """Devuelve las ordenes registradas en SQLite en tiempo real"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, item, total, method, phone, state FROM cielo_orders ORDER BY rowid DESC")
    rows = cursor.fetchall()
    conn.close()
    
    orders = []
    for r in rows:
        orders.append({
            "id": r[0],
            "item": r[1],
            "total": r[2],
            "method": r[3],
            "phone": r[4],
            "state": r[5]
        })
    return orders

@app.patch("/api/orders/{order_id}/state")
async def update_order_state(order_id: str, payload: StateUpdate):
    """Actualiza el estado de una orden desde el Kanban"""
    valid_states = ["recibido", "preparando", "encamino", "entregado"]
    if payload.state not in valid_states:
        raise HTTPException(status_code=400, detail="Estado inválido")
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Extraer el teléfono antes de actualizar para enviar notificación
    cursor.execute("SELECT phone FROM cielo_orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Orden no encontrada")
        
    cursor.execute("UPDATE cielo_orders SET state = ? WHERE id = ?", (payload.state, order_id))
    conn.commit()
    conn.close()
    
    phone = row[0]
    
    # Enviar notificaciones de actualización al usuario usando Graph API
    if payload.state == "preparando":
        await send_whatsapp_message(phone, f"👨‍🍳 ¡Tu pedido ha entrado a cocina! Ya mismo lo estamos preparando.")
    elif payload.state == "encamino":
        await send_whatsapp_message(phone, f"🛵 ¡Tu pedido ya va en camino hacia ti! Atento al repartidor.")
    elif payload.state == "entregado":
        await send_whatsapp_message(phone, f"✅ ¡Pedido entregado! Muchas gracias por confiar en Cielo Food House.")
        
    return {"status": "success", "new_state": payload.state}

@app.get("/logo.png")
def serve_logo_png():
    if os.path.exists("logo.png"):
        return FileResponse("logo.png")
    return PlainTextResponse("not found", status_code=404)

@app.get("/logo.jpg")
def serve_logo_jpg():
    if os.path.exists("logo.jpg"):
        return FileResponse("logo.jpg")
    elif os.path.exists("logo.jpeg"):
        return FileResponse("logo.jpeg")
    return PlainTextResponse("not found", status_code=404)

@app.get("/")
def serve_landing():
    """Aloja la Landing Provisional Próximamente en la página principal"""
    if os.path.exists("coming_soon.html"):
        return FileResponse("coming_soon.html")
    # Redirección de respaldo
    if os.path.exists("cielo_food_house.html"):
        return FileResponse("cielo_food_house.html")
    return PlainTextResponse("El archivo coming_soon.html no fue subido al servidor.")

@app.get("/kanban")
def serve_kanban():
    """Aloja el Frontend Kanban de administración"""
    if os.path.exists("cielo_food_house.html"):
        return FileResponse("cielo_food_house.html")
    return PlainTextResponse("El archivo cielo_food_house.html no fue subido al servidor junto con el bot.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
