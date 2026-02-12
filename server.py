import socket
import threading
import time
from cryptography.fernet import Fernet
from deep_translator import GoogleTranslator


try:
    import pyttsx3
    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False

AUTO_REPLY_DELAY = 6  # seconds before auto-reply triggers


def simple_auto_reply(text):
    t = text.lower()
    if any(g in t for g in ("hello", "hi", "hey")):
        return "Hello! I am currently away, I'll reply properly soon."
    if "how are you" in t or "how r u" in t:
        return "I'm fine, thank you! How about you?"
    if "price" in t or "cost" in t:
        return "Can you share more details so I can help with that?"
    if "bye" in t or "exit" in t:
        return "Goodbye! Talk later."
    return "Thanks for your message. I'll get back to you shortly."


class MultiServer:
    def __init__(self, host='0.0.0.0', port=9999):
        self.host = host
        self.port = port
        self.sock = None
        self.clients = {}  # client_id -> dict with conn, addr, cipher, lang, tts_on, last_manual_reply_time
        self.next_client_id = 1
        self.clients_lock = threading.Lock()
        self.selected_client = None  # client_id selected by server operator
        self.engine = None
        if TTS_AVAILABLE:
            try:
                self.engine = pyttsx3.init()
            except Exception:
                self.engine = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        print(f"Server listening on {self.host}:{self.port} ...")
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self._operator_loop()

    def _accept_loop(self):
        while True:
            try:
                conn, addr = self.sock.accept()
                with self.clients_lock:
                    client_id = self.next_client_id
                    self.next_client_id += 1
                print(f"New client connecting from {addr} -> assigned id {client_id}")

                # generate per-client key and send to client (demo)
                key = Fernet.generate_key()
                try:
                    conn.send(key)
                except Exception as e:
                    print("Failed to send key to client:", e)
                    conn.close()
                    continue

                cipher = Fernet(key)
                client_info = {
                    "conn": conn,
                    "addr": addr,
                    "cipher": cipher,
                    "lang": "en",          # target translation language for server side when viewing messages from this client
                    "tts_on": False,
                    "last_manual_reply_time": 0.0
                }
                with self.clients_lock:
                    self.clients[client_id] = client_info

                # Start client listener thread
                threading.Thread(target=self._client_listener, args=(client_id,), daemon=True).start()
                print(f"Client {client_id} connected.")
            except Exception as e:
                print("Accept loop error:", e)
                break

    def _client_listener(self, client_id):
        conn = self.clients[client_id]["conn"]
        cipher = self.clients[client_id]["cipher"]
        while True:
            try:
                data = conn.recv(8192)
                if not data:
                    print(f"Client {client_id} disconnected.")
                    with self.clients_lock:
                        if client_id in self.clients:
                            del self.clients[client_id]
                    if self.selected_client == client_id:
                        self.selected_client = None
                    break
                try:
                    msg = cipher.decrypt(data).decode()
                except Exception:
                    print(f"Client {client_id}: failed to decrypt incoming data.")
                    continue

                print(f"\n<< [Client {client_id}] {msg}")

                # Translate to server's chosen language for that client
                lang = self.clients[client_id].get("lang", "en")
                try:
                    translated = GoogleTranslator(target=lang).translate(msg)
                except Exception:
                    translated = msg
                print(f"[Translated → {lang}] {translated}")

                # Optionally speak on server console if enabled for this client
                if self.clients[client_id].get("tts_on") and self.engine:
                    try:
                        self.engine.say(translated)
                        self.engine.runAndWait()
                    except Exception:
                        pass

                # start auto-reply timer for that client
                incoming_time = time.time()
                threading.Thread(target=self._auto_reply_timer, args=(client_id, incoming_time, msg), daemon=True).start()

            except Exception as e:
                print(f"Listener error for client {client_id}:", e)
                with self.clients_lock:
                    if client_id in self.clients:
                        del self.clients[client_id]
                break

    def _auto_reply_timer(self, client_id, incoming_time, incoming_msg):
        time.sleep(AUTO_REPLY_DELAY)
        with self.clients_lock:
            ci = self.clients.get(client_id)
            if not ci:
                return
            if ci["last_manual_reply_time"] > incoming_time:
                return
        reply = simple_auto_reply(incoming_msg)
        print(f"\n[Auto-reply -> Client {client_id}] {reply}")
        self.send_to_client(client_id, reply)

    def send_to_client(self, client_id, plaintext):
        with self.clients_lock:
            ci = self.clients.get(client_id)
            if not ci:
                print(f"No such client: {client_id}")
                return
            conn = ci["conn"]
            cipher = ci["cipher"]
            # mark manual reply time
            ci["last_manual_reply_time"] = time.time()
        try:
            token = cipher.encrypt(plaintext.encode())
            conn.send(token)
        except Exception as e:
            print(f"Failed to send to client {client_id}:", e)

    def broadcast(self, plaintext):
        with self.clients_lock:
            ids = list(self.clients.keys())
        for cid in ids:
            self.send_to_client(cid, plaintext)

    def _operator_loop(self):
        print("\nServer operator commands: /list, /select <id>, /all <message>, /lang <lang_code> (for selected), /quit")
        while True:
            try:
                raw = input("\nServer> ").strip()
            except KeyboardInterrupt:
                print("\nShutting down server.")
                break
            if not raw:
                continue
            if raw == "/list":
                with self.clients_lock:
                    if not self.clients:
                        print("No connected clients.")
                    else:
                        for cid, info in self.clients.items():
                            print(f" - id={cid} addr={info['addr']} lang={info.get('lang','en')}")
                continue
            if raw.startswith("/select "):
                try:
                    cid = int(raw.split(" ", 1)[1])
                except Exception:
                    print("Usage: /select <client_id>")
                    continue
                with self.clients_lock:
                    if cid in self.clients:
                        self.selected_client = cid
                        print(f"Selected client {cid}.")
                    else:
                        print(f"No client with id {cid}.")
                continue
            if raw.startswith("/all "):
                msg = raw.split(" ", 1)[1]
                self.broadcast(msg)
                print("Broadcasted.")
                continue
            if raw.startswith("/lang "):
                if self.selected_client is None:
                    print("Select a client first with /select <id>")
                    continue
                new_lang = raw.split(" ", 1)[1].strip().lower()
                with self.clients_lock:
                    if self.selected_client in self.clients:
                        self.clients[self.selected_client]["lang"] = new_lang
                        print(f"Set translation language for client {self.selected_client} to '{new_lang}'")
                    else:
                        print("Selected client not found.")
                continue
            if raw in ("/quit", "/exit"):
                print("Closing all connections and exiting.")
                with self.clients_lock:
                    for cid, info in list(self.clients.items()):
                        try:
                            info["conn"].close()
                        except Exception:
                            pass
                    self.clients.clear()
                try:
                    self.sock.close()
                except Exception:
                    pass
                break
            # otherwise treat as message to selected client
            if self.selected_client is None:
                print("No client selected. Use /list and /select <id> or use /all <message> to broadcast.")
                continue
            # send message to selected client
            self.send_to_client(self.selected_client, raw)
            print(f"Sent to client {self.selected_client}.")

if __name__ == "__main__":
    server = MultiServer(host='localhost', port=9999)
    server.start()
