import os
import httpx
import sqlite3
import json
import uuid
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# --- IMPORTS DE GOOGLE GENAI ---
from google import genai
from google.genai import types

load_dotenv()

app = FastAPI(title="Cielo Food House - WhatsApp Bot & Kanban MVP")

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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Cliente Gemini
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
USER_CARTS = {}

# --- CATÁLOGO ESTRUCTURADO Y ESCALABLE (AI-READY) ---
MENU_CATALOG = {
    "1": {
        "id": "mix_fiestero", "name": "Mix Fiestero", "price": 38000, "type": "configurable",
        "desc": "15 mini tequeños, 15 mini pastelitos, 15 mini empanaditas",
        "options": ["Todo Pollo", "Todo Carne Picada", "Mitad y Mitad"]
    },
    "2": {
        "id": "tequenos", "name": "Tequeños", "type": "category",
        "items": {"Docena de Tequeños": 20000, "25 Tequeños de fiesta": 21000}
    },
    "3": {
        "id": "mandocas", "name": "Mandocas (x unidad)", "price": 2000, "type": "simple"
    },
    "4": {
        "id": "pasteles", "name": "Pasteles", "type": "category",
        "items": {"Pastel de Pizza": 2000, "Pastel de Pollo": 2000, "Pastel de Carne": 2000, "Pastel de Papa con queso": 2000}
    },
    "5": {
        "id": "empanadas", "name": "Empanadas", "type": "category",
        "items": {"Empanada de Pollo": 3000, "Empanada de Carne Picada": 3000, "Empanada de Carne Mechada": 3000, "Empanada de Cazón": 3000, "Empanada Papa con queso": 3000}
    },
    "6": {
        "id": "bebidas", "name": "Bebidas", "type": "category",
        "items": {"Malta 354ml": 1800, "Uvita 500ml": 1900, "Colita 500 ml": 1900}
    },
    "7": {
        "id": "adicionales", "name": "Adicionales", "type": "category",
        "items": {"Salsa": 2000}
    }
}

PAYMENT_METHODS = ["Mercado Pago", "Efectivo", "Transferencia Bancaria"]

# --- WEBSOCKETS KANBAN ---
class KanbanConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_order(self, order_data: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(order_data)
            except:
                pass

kanban_manager = KanbanConnectionManager()

@app.websocket("/ws/kanban")
async def websocket_kanban(websocket: WebSocket):
    await kanban_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        kanban_manager.disconnect(websocket)

# --- HERRAMIENTAS PARA GEMINI ---
def agregar_al_carrito(telefono: str, producto: str, cantidad: int, precio_unitario: float) -> str:
    """Registra un producto en el carrito del cliente.
    Args:
        telefono: El número de teléfono del cliente (debe pasarse siempre).
        producto: El nombre descriptivo del producto y su configuración (ej. "Mix Fiestero (Todo Pollo)").
        cantidad: Cantidad de este producto.
        precio_unitario: Precio por unidad según el catálogo.
    """
    if telefono not in USER_CARTS:
        USER_CARTS[telefono] = []
        
    subtotal = precio_unitario * cantidad
    USER_CARTS[telefono].append({
        "name": producto,
        "price": precio_unitario,
        "qty": cantidad
    })
    
    total = sum(item["price"] * item["qty"] for item in USER_CARTS[telefono])
    
    return json.dumps({
        "status": "success",
        "producto": producto,
        "cantidad": cantidad,
        "subtotal": subtotal,
        "mensaje": f"Se agregaron {cantidad} {producto} al carrito.",
        "total_carrito_actual": total
    })

def finalizar_pedido(telefono: str, metodo_pago: str) -> str:
    """Finaliza el pedido actual del cliente y lo guarda en la base de datos.
    Se debe llamar SOLO cuando el cliente ha confirmado su carrito, ha elegido un método de pago y ha proporcionado su dirección.
    Args:
        telefono: El número de teléfono del cliente.
        metodo_pago: El método de pago elegido (Efectivo, Mercado Pago, Transferencia Bancaria).
    """
    cart = USER_CARTS.get(telefono, [])
    if not cart:
        return json.dumps({"status": "error", "mensaje": "El carrito está vacío."})
        
    total_price = sum(item["price"] * item["qty"] for item in cart)
    items_summary = ", ".join([f"{i['qty']}x {i['name']}" for i in cart])
    
    new_id = "CL-" + str(uuid.uuid4().hex[:5]).upper()
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO cielo_orders (id, item, total, method, phone, state)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (new_id, items_summary, total_price, metodo_pago, telefono, "recibido"))
    conn.commit()
    conn.close()
    
    # Vaciar carrito
    USER_CARTS[telefono] = []
    
    # IMPORTANTE: No podemos hacer await aquí directamente porque es una función síncrona llamada por Gemini.
    # En su lugar, dejaremos un flag en el estado para emitir el WS luego, o mejor, 
    # FastAPI lo permite si usamos run_in_threadpool o asyncio.create_task si tuvieramos el loop.
    # Por simplicidad, devolveremos el new_id y el caller se encarga del WS o lo emitimos por un hack.
    
    return json.dumps({
        "status": "success", 
        "order_id": new_id,
        "item": items_summary,
        "total": total_price,
        "method": metodo_pago,
        "phone": telefono,
        "state": "recibido",
        "mensaje": f"Pedido {new_id} confirmado exitosamente."
    })

SYSTEM_INSTRUCTION = f"""
Eres el asistente virtual de Cielo Food House en Campana. 
Atiendes de Lunes a Viernes 08:30-22:00 y fines de semana 08:30-23:00.
Tu objetivo es tomar los pedidos de los clientes, guiarlos por el menú de forma amigable, estilo venezolano (usa emojis, sé cálido).

Aquí está nuestro Catálogo:
{json.dumps(MENU_CATALOG, indent=2, ensure_ascii=False)}
Métodos de pago aceptados: {PAYMENT_METHODS}

Reglas:
1. Siempre muestra el menú si el usuario saluda o pregunta qué hay.
2. Si un producto tiene "options" (como el Mix Fiestero), PREGUNTA al usuario qué opción desea antes de agregarlo al carrito.
3. SIEMPRE usa la herramienta `agregar_al_carrito` para añadir items al pedido. Pide la cantidad al usuario. No calcules totales tú mismo. 
4. El argumento `telefono` de las herramientas debe ser siempre el número de teléfono con el que estás hablando.
5. Después de agregar, pregunta si desean algo más o proceder a pagar.
6. Para pagar, pregunta el método de pago y la DIRECCIÓN EXACTA de envío (o si retiran en el local).
7. Cuando el usuario confirme el pago y dirección, usa la herramienta `finalizar_pedido`.

Aviso Legal: Nunca inventes precios ni productos. Limítate al catálogo.
"""

# Historial de chats para mantener contexto
CHAT_SESSIONS = {}

async def process_whatsapp_ai(sender_id: str, message_text: str):
    """Procesamiento con Gemini"""
    if not client:
        return "El sistema de IA no está configurado (falta GEMINI_API_KEY). Por favor contacta al administrador."
        
    if sender_id not in CHAT_SESSIONS:
        chat = client.chats.create(
            model='gemini-1.5-flash',
            config=types.GenerateContentConfig(
                tools=[agregar_al_carrito, finalizar_pedido],
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3
            )
        )
        CHAT_SESSIONS[sender_id] = chat
    else:
        chat = CHAT_SESSIONS[sender_id]

    # Pasamos explícitamente que el teléfono del usuario es sender_id
    prompt = f"El usuario (Teléfono: {sender_id}) dice: {message_text}"
    
    try:
        response = chat.send_message(prompt)
        # Check if a tool was called and returned an order creation to broadcast via websocket
        # We can scan the chat history for function calls, but an easier way is 
        # checking the latest SQLite orders if needed, or if response text contains confirmation.
        # Actually, when `finalizar_pedido` is called, it inserts into DB. 
        # We'll just fetch the latest order for this phone in the last few seconds and broadcast if new.
    except Exception as e:
        print(f"Error de Gemini: {e}")
        return "Lo siento, tuve un problema procesando tu mensaje. ¿Puedes repetirlo?"
        
    return response.text

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
    
    async with httpx.AsyncClient() as client_http:
        try:
            r = await client_http.post(url, headers=headers, json=payload)
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

import asyncio

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
            
            # Obtener cantidad de órdenes antes del procesamiento
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM cielo_orders WHERE phone=?", (sender_id,))
            orders_before = cur.fetchone()[0]
            conn.close()
            
            response_text = await process_whatsapp_ai(sender_id, msg_text)
            await send_whatsapp_message(sender_id, response_text)
            
            # Revisar si se creó una nueva orden para emitir el WebSocket
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM cielo_orders WHERE phone=?", (sender_id,))
            orders_after = cur.fetchone()[0]
            if orders_after > orders_before:
                cur.execute("SELECT id, item, total, method, phone, state FROM cielo_orders WHERE phone=? ORDER BY rowid DESC LIMIT 1", (sender_id,))
                new_order = cur.fetchone()
                if new_order:
                    order_data = {
                        "id": new_order[0],
                        "item": new_order[1],
                        "total": new_order[2],
                        "method": new_order[3],
                        "phone": new_order[4],
                        "state": new_order[5]
                    }
                    asyncio.create_task(kanban_manager.broadcast_order(order_data))
            conn.close()
            
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return {"status": "success"}

class StateUpdate(BaseModel):
    state: str

@app.get("/api/orders")
def get_orders():
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
    valid_states = ["recibido", "preparando", "encamino", "entregado"]
    if payload.state not in valid_states:
        raise HTTPException(status_code=400, detail="Estado inválido")
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT phone FROM cielo_orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Orden no encontrada")
        
    cursor.execute("UPDATE cielo_orders SET state = ? WHERE id = ?", (payload.state, order_id))
    conn.commit()
    
    # Broadcast status change
    cursor.execute("SELECT id, item, total, method, phone, state FROM cielo_orders WHERE id = ?", (order_id,))
    updated_order = cursor.fetchone()
    conn.close()
    
    if updated_order:
        order_data = {
            "id": updated_order[0],
            "item": updated_order[1],
            "total": updated_order[2],
            "method": updated_order[3],
            "phone": updated_order[4],
            "state": updated_order[5]
        }
        await kanban_manager.broadcast_order(order_data)
    
    phone = row[0]
    
    if payload.state == "preparando":
        await send_whatsapp_message(phone, f"👨‍🍳 ¡Tu pedido ha entrado a cocina! Ya mismo lo estamos preparando.")
    elif payload.state == "encamino":
        await send_whatsapp_message(phone, f"🛵 ¡Tu pedido ya va en camino hacia ti! Atento al repartidor.")
    elif payload.state == "entregado":
        await send_whatsapp_message(phone, f"✅ ¡Pedido entregado! Muchas gracias por confiar en Cielo Food House.")
        
    return {"status": "success", "new_state": payload.state}

@app.get("/logo.png")
def serve_logo_png():
    if os.path.exists("logo.png"): return FileResponse("logo.png")
    return PlainTextResponse("not found", status_code=404)

@app.get("/logo.jpg")
def serve_logo_jpg():
    if os.path.exists("logo.jpg"): return FileResponse("logo.jpg")
    elif os.path.exists("logo.jpeg"): return FileResponse("logo.jpeg")
    return PlainTextResponse("not found", status_code=404)

@app.get("/")
def serve_landing():
    if os.path.exists("coming_soon.html"): return FileResponse("coming_soon.html")
    if os.path.exists("cielo_food_house.html"): return FileResponse("cielo_food_house.html")
    return PlainTextResponse("El archivo coming_soon.html no fue subido al servidor.")

@app.get("/kanban")
def serve_kanban():
    if os.path.exists("cielo_food_house.html"): return FileResponse("cielo_food_house.html")
    return PlainTextResponse("El archivo cielo_food_house.html no fue subido al servidor junto con el bot.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
