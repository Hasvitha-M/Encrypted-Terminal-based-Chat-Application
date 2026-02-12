import socket
import threading
from cryptography.fernet import Fernet
from deep_translator import GoogleTranslator


try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except Exception:
    SR_AVAILABLE = False

try:
    import pyttsx3
    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False


def recognize_speech_from_mic():
    if not SR_AVAILABLE:
        print("SpeechRecognition not available.")
        return ""
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening... (speak now)")
        audio = r.listen(source, timeout=5, phrase_time_limit=8)
    try:
        text = r.recognize_google(audio)
        print("→ Recognized:", text)
        return text
    except sr.UnknownValueError:
        print("Could not understand audio.")
        return ""
    except sr.RequestError:
        print("Speech recognition service error.")
        return ""


class Client:
    def __init__(self, host='localhost', port=9999):
        self.host = host
        self.port = port
        self.sock = None
        self.cipher = None
        self.client_lang = "en"
        self.tts_on = False
        self.engine = None
        self.sr_on = False

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(f"Connecting to server at {self.host}:{self.port} ...")
        self.sock.connect((self.host, self.port))
        print("Connected to server.")

        # read key from server (first recv)
        key = self.sock.recv(4096)
        self.cipher = Fernet(key)
        print("Encryption key received.")

        # choose incoming translation language
        self.client_lang = input("Translate incoming messages to which language? (e.g., en, hi, fr): ").strip().lower() or "en"

        if TTS_AVAILABLE:
            ans = input("Enable text-to-speech for received messages? (y/n): ").strip().lower()
            self.tts_on = ans.startswith('y')
            if self.tts_on:
                try:
                    self.engine = pyttsx3.init()
                except Exception:
                    self.engine = None
        else:
            print("pyttsx3 not available; TTS disabled.")

        if SR_AVAILABLE:
            ans = input("Enable speech-to-text for sending messages? (y/n): ").strip().lower()
            self.sr_on = ans.startswith('y')
            if self.sr_on:
                print("Speech input enabled. Make sure microphone works.")
        else:
            print("SpeechRecognition not available; voice input disabled.")

        # start receiver thread
        threading.Thread(target=self._receive_loop, daemon=True).start()
        self._send_loop()

    def _receive_loop(self):
        while True:
            try:
                data = self.sock.recv(8192)
                if not data:
                    print("Server disconnected.")
                    break
                try:
                    msg = self.cipher.decrypt(data).decode()
                except Exception:
                    print("Failed to decrypt incoming message.")
                    continue
                print(f"\n<< Server: {msg}")
                try:
                    translated = GoogleTranslator(target=self.client_lang).translate(msg)
                except Exception:
                    translated = msg
                print(f"[Translated → {self.client_lang}] {translated}")
                if self.tts_on and self.engine:
                    try:
                        self.engine.say(translated)
                        self.engine.runAndWait()
                    except Exception:
                        pass
            except Exception as e:
                print("Receive error:", e)
                break

    def _send_loop(self):
        try:
            while True:
                choice = input("Type (t) or Speak (s) ? (or '/quit' to exit): ").strip().lower()
                if choice in ("/quit", "/exit"):
                    break
                if choice == 's':
                    if not self.sr_on:
                        print("Speech input not enabled. Choose 't' to type.")
                        continue
                    text = recognize_speech_from_mic()
                    if not text:
                        continue
                else:
                    text = input("You: ").strip()

                if not text:
                    continue
                # client-side /lang command to change how client sees incoming messages
                if text.startswith("/lang "):
                    new_lang = text.split(" ", 1)[1].strip().lower()
                    self.client_lang = new_lang
                    print(f"✔ Local translation language changed to '{self.client_lang}'")
                    continue

                token = self.cipher.encrypt(text.encode())
                self.sock.send(token)
                if text.lower() in ('exit', 'quit', 'bye'):
                    print("Disconnecting.")
                    break
        except KeyboardInterrupt:
            print("\nClient interrupted.")
        finally:
            try:
                self.sock.close()
            except Exception:
                pass


if __name__ == "__main__":
    c = Client(host='localhost', port=9999)
    c.start()
