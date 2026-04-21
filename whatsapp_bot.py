import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Cielo Food House - WhatsApp Bot MVP")

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "cielo_secret_token")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "").replace("Bearer ", "").strip()
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

# --- MEMORIA Y ESTADOS SIMULADOS ---
# En un entorno real, esto iría en Redis o PostgreSQL (como tu app anterior).
USER_STATES = {}
USER_CARTS = {}

MENU = {
    "1": {"name": "Mix Fiestero", "price": 38000},
    "2": {"name": "Docena de Tequeños", "price": 20000},
    "3": {"name": "25 Tequeños de Fiesta", "price": 21000},
    "4": {"name": "Empanadas (Pollo/Carne/Cazón)", "price": 3000},
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

def process_whatsapp_state(sender_id: str, message_text: str):
    """Máquina de Estados Conversacional (Bot) para toma de pedidos"""
    
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
        total = calculate_total(USER_CARTS.get(sender_id, []))
        method = USER_STATES.get(f"{sender_id}_payment")
        
        # Simulación: Aquí es donde enviaríamos la petición HTTP POST a FastAPI para pintarlo en el tablero Canvas / Kanban Base de Datos
        print(f"[NUEVO PEDIDO] WhatsApp: {sender_id} | Total: ${total} | Pago: {method}")
        
        USER_STATES[sender_id] = "START"
        USER_CARTS[sender_id] = []
        return "¡Pedido Confirmado! ✅ Tu orden ha entrado directamente a nuestra cocina. Pronto nos pondremos en contacto contigo si falta algún detalle. ¡Gracias por elegir el buen sabor!"

    # Fallback
    USER_STATES[sender_id] = "START"
    return "Ups, me perdí. Vamos de nuevo... Escribe cualquier cosa para empezar."


# --- ENDPOINTS FASTAPI (WEBHOOK META) ---

@app.get("/api/whatsapp/webhook")
async def verify_webhook(request: Request):
    """Endpoint para autenticar Webhook de Meta (WhatsApp Cloud API)."""
    hub_mode = request.query_params.get("hub.mode")
    hub_challenge = request.query_params.get("hub.challenge")
    hub_verify_token = request.query_params.get("hub.verify_token")

    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(str(hub_challenge))
    raise HTTPException(status_code=403, detail="Token no válido")


async def send_whatsapp_message(to_number: str, text: str):
    """Envia mensaje HTTP a Graph API de Meta."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print(f"[SIMULADO EN LOCAL - NO KEY] Enviar a {to_number}: {text}")
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


@app.post("/api/whatsapp/webhook")
async def receive_webhook(request: Request):
    """Recibe mensajes de los usuarios y activa la Máquina de Estados."""
    data = await request.json()
    
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if messages:
            message = messages[0]
            sender_id = message.get("from")
            # Extraer texto de forma resiliente
            msg_text = message.get("text", {}).get("body", "IMAGEN/DOCUMENTO")
            
            # 1. Calcular nueva respuesta según estado
            response_text = process_whatsapp_state(sender_id, msg_text)
            
            # 2. Enviar respuesta real a WhatsApp
            await send_whatsapp_message(sender_id, response_text)
            
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    # Inicia el bot en el puerto 8000 para poder ser expuesto por ngrok
    print("Iniciando Bot WhatsApp Cielo Food House...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
