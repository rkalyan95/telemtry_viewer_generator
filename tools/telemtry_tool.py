import importlib.util
import json
import os
import sys
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import serial
import struct
import subprocess
import threading
import time
from plotter import PlotWindow

CODEGEN_SCRIPT = os.path.join(os.path.dirname(__file__), "telemetry_codegen.py")

try:
    import generatefiles.telemetry_config_ui as config_ui
except ImportError:
    config_ui_path = os.path.join(os.path.dirname(__file__), "generatefiles", "telemetry_config_ui.py")
    spec = importlib.util.spec_from_file_location("telemetry_config_ui", config_ui_path)
    config_ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_ui)

def calculate_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1: crc = (crc >> 1) ^ 0xA001
            else:         crc >>= 1
    return crc & 0xFFFF

class TelemetryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telemetry Monitor & Config")
        self.root.geometry("1180x760")

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        self.root.option_add("*Font", ("Segoe UI", 9))
        self.device_registry = {}
        self.ser = None
        self.serial_connected = False
        self.handshake_done = False
        self.kill_listen_thread = False
        self.listen_thread = None
        self.config_data = None
        self.config_path = None
        self.is_logging = False
        self.clean_file = None
        self.raw_file = None
        try:
            with open("telemetry_config_example.json", "r", encoding="utf-8") as f:
                self.config_data = json.load(f)
        except FileNotFoundError:
            self.config_data = {"sensors": []}
            print("Config file not found!")
            
        self.subscribers = []
        self.create_widgets()
        self.attempt_serial_connection()

    def create_widgets(self):
        # Top Configuration Bar
        input_frame = ttk.LabelFrame(self.root, text=" Hardware Link Controls ", padding=10)
        input_frame.pack(fill="x", padx=15, pady=5)

        self.btn_disconnect = ttk.Button(input_frame, text="🛑 Disconnect Port", command=self.disconnect_serial)
        self.btn_disconnect.grid(row=0, column=0, padx=5, pady=5)

        self.btn_restart = ttk.Button(input_frame, text="🔄 Restart & Sync Loop", command=self.restart_and_sync)
        self.btn_restart.grid(row=0, column=1, padx=5, pady=5)

        self.btn_generate = ttk.Button(input_frame, text="📄 Generate C Files", command=self.generate_config_files)
        self.btn_generate.grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(input_frame, text="Struct Alignment Mode:").grid(row=0, column=3, padx=(20, 5), pady=5)
        self.alignment_var = tk.StringVar(value="32-bit ARM (Cortex-M)")
        self.combo_alignment = ttk.Combobox(
            input_frame, 
            textvariable=self.alignment_var, 
            values=["Standard Tightly Packed (=)", "32-bit ARM (Cortex-M)"],
            state="readonly",
            width=24
        )
        self.combo_alignment.grid(row=0, column=4, padx=5, pady=5)

        button_frame = ttk.Frame(input_frame)
        button_frame.grid(row=1, column=0, columnspan=5, pady=(8, 0), sticky="w")

        ttk.Button(button_frame, text="📂 Load JSON", command=self.load_json).grid(row=0, column=0, padx=3, pady=2)
        ttk.Button(button_frame, text="💾 Save JSON", command=self.save_json).grid(row=0, column=1, padx=3, pady=2)
        ttk.Button(button_frame, text="➕ Add Sensor", command=self.add_sensor).grid(row=0, column=2, padx=3, pady=2)
        ttk.Button(button_frame, text="✏️ Edit Sensor", command=self.edit_sensor).grid(row=0, column=3, padx=3, pady=2)
        ttk.Button(button_frame, text="🗑 Remove Sensor", command=self.remove_sensor).grid(row=0, column=4, padx=3, pady=2)
        ttk.Button(button_frame, text="🧹 Clear Screen", command=self.clear_screen).grid(row=0, column=5, padx=3, pady=2)
        # Inside create_widgets, within the button_frame section:
        ttk.Button(button_frame, text="🔴 Toggle Logging", command=self.toggle_logging).grid(row=0, column=6, padx=3, pady=2)
        ttk.Button(button_frame, text="📈 PLOT DATA", 
           command=lambda: PlotWindow(self.root, self)).grid(row=0, column=7, padx=3, pady=2)
        self.config_label = tk.Label(input_frame, text="Config: none", fg="#333")
        self.config_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(10,0))

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(self.root, text="Initializing...", font=("Segoe UI", 10, "bold"))
        self.status_label.pack(anchor="w", padx=15, pady=2)

        config_frame = ttk.LabelFrame(self.root, text=" Telemetry Config Editor ", padding=10)
        config_frame.pack(fill="x", padx=15, pady=5)

        self.sensor_list = tk.Listbox(config_frame, height=5, selectmode="browse")
        self.sensor_list.pack(fill="both", expand=True, side="left")
        sensor_scroll = ttk.Scrollbar(config_frame, command=self.sensor_list.yview)
        sensor_scroll.pack(side="right", fill="y")
        self.sensor_list.configure(yscrollcommand=sensor_scroll.set)

        # Device Registration Grid
        table_frame = ttk.LabelFrame(self.root, text=" Devices Registered by Controller ", padding=10)
        table_frame.pack(fill="x", padx=15, pady=5)

        self.tree = ttk.Treeview(table_frame, columns=("ID", "Name", "Format", "Handshake"), show="headings", height=5)
        self.tree.heading("ID", text="Device ID")
        self.tree.heading("Name", text="Device Name")
        self.tree.heading("Format", text="Data Format Layout")
        self.tree.heading("Handshake", text="Status Confirmation")
        
        self.tree.column("ID", width=80, anchor="center")
        self.tree.column("Name", width=120, anchor="center")
        self.tree.column("Format", width=140, anchor="center")
        self.tree.column("Handshake", width=230, anchor="center")
        self.tree.pack(fill="x")

        # Layout Main Frame Side-by-Side Split View
        display_container = ttk.Frame(self.root)
        display_container.pack(fill="both", expand=True, padx=15, pady=10)

        # LEFT SIDE: Clean Parsed Output Feed
        left_frame = ttk.LabelFrame(display_container, text=" Clean Processed Telemetry Data Stream ", padding=5)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self.log_text = tk.Text(left_frame, wrap="word", background="#1A1A1A", foreground="#00FF00", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, side="left")
        left_scroll = ttk.Scrollbar(left_frame, command=self.log_text.yview)
        left_scroll.pack(fill="y", side="right")
        self.log_text.config(yscrollcommand=left_scroll.set)

        # RIGHT SIDE: Raw Ingress Serial Data Buffer Feed
        right_frame = ttk.LabelFrame(display_container, text=" Raw Inbound Link Buffer Hex & ASCII Monitor ", padding=5)
        right_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        self.raw_text = tk.Text(right_frame, wrap="word", background="#0C0C0C", foreground="#00FFFF", font=("Consolas", 9))
        self.raw_text.pack(fill="both", expand=True, side="left")
        right_scroll = ttk.Scrollbar(right_frame, command=self.raw_text.yview)
        right_scroll.pack(fill="y", side="right")
        self.raw_text.config(yscrollcommand=right_scroll.set)

        self.status_bar = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w", padding=(6,4))
        self.status_bar.pack(fill="x", padx=15, pady=(0,5))

    def update_status(self, message, color="black"):
        self.status_label.config(text=message, foreground=color)
        self.status_var.set(message)

    def log_message(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.write_to_log(self.clean_file, f"[{time.strftime('%H:%M:%S')}] {message}")
        
    def log_raw_bytes(self, raw_bytes):
        # Formats incoming packets cleanly into both hex and string segments
        hex_dump = " ".join(f"{b:02X}" for b in raw_bytes)
        ascii_dump = "".join(chr(b) if 32 <= b <= 126 else "." for b in raw_bytes)
        timestamp = time.strftime("%H:%M:%S")
        
        self.raw_text.insert(tk.END, f"[{timestamp}] HEX: {hex_dump}\n             ASCII: {ascii_dump}\n")
        self.raw_text.see(tk.END)
        self.write_to_log(self.raw_file, f"[{timestamp}] HEX: {hex_dump}")
        
    def generate_config_files(self):
        if self.config_data:
            config = self.config_data
        else:
            config_path = filedialog.askopenfilename(
                title="Select telemetry config JSON",
                filetypes=[("JSON files", "*.json"), ("All files", "*")]
            )
            if not config_path:
                return
            try:
                with open(config_path, "r", encoding="utf-8") as handle:
                    config = json.load(handle)
            except Exception as exc:
                messagebox.showerror("Load error", f"Unable to load config:\n{exc}")
                return

        out_dir = filedialog.askdirectory(
            title="Select output directory for generated files"
        )
        if not out_dir:
            out_dir = os.path.join(os.path.dirname(__file__), "generatedconfigs")

        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Output Error", f"Unable to create output directory:\n{exc}")
            return

        if not os.path.exists(CODEGEN_SCRIPT):
            messagebox.showerror("Generator Missing", f"Cannot find telemetry generator:\n{CODEGEN_SCRIPT}")
            return

        try:
            spec = importlib.util.spec_from_file_location("telemetry_codegen", CODEGEN_SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            header_path, source_path = module.generate_files(config, out_dir)
            messagebox.showinfo("Generation Complete", f"Generated:\n{header_path}\n{source_path}")
            self.update_status("Generated C files successfully.", "green")
        except Exception as exc:
            tb = traceback.format_exc()
            messagebox.showerror("Generation Failed", f"{exc}\n\n{tb}")
            self.update_status("Generation failed.", "red")

    def clear_screen(self):
        self.log_text.delete("1.0", tk.END)
        self.raw_text.delete("1.0", tk.END)

    def refresh_sensor_list(self):
        self.sensor_list.delete(0, tk.END)
        if not self.config_data:
            return
        for sensor in self.config_data.get("sensors", []):
            display = f"{sensor['id']}: {sensor['name']} ({sensor['data_type']})"
            if sensor.get("description"):
                display += f" - {sensor['description']}"
            self.sensor_list.insert(tk.END, display)

    def load_json(self):
        path = filedialog.askopenfilename(
            title="Open telemetry JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                self.config_data = json.load(handle)
        except Exception as exc:
            messagebox.showerror("Load error", f"Unable to load config:\n{exc}")
            return
        self.config_path = path
        self.config_label.config(text=f"Config: {os.path.basename(path)}")
        self.refresh_sensor_list()
        self.update_status(f"Loaded config: {os.path.basename(path)}", "green")

    def save_json(self):
        if not self.config_data:
            messagebox.showwarning("No config", "Load or create a telemetry configuration first.")
            return False
        path = filedialog.asksaveasfilename(
            title="Save telemetry JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile=os.path.basename(self.config_path or "telemetry_config.json"),
            initialdir=os.path.dirname(self.config_path or os.getcwd())
        )
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.config_data, handle, indent=2)
                handle.write("\n")
        except Exception as exc:
            messagebox.showerror("Save error", f"Unable to save config:\n{exc}")
            return False
        self.config_path = path
        self.config_label.config(text=f"Config: {os.path.basename(path)}")
        messagebox.showinfo("Saved", f"Configuration saved to {path}")
        self.update_status(f"Saved config: {os.path.basename(path)}", "green")
        return True

    def add_sensor(self):
        editor = config_ui.SensorEditor(self.root, "Add sensor")
        if editor.result:
            self.config_data = self.config_data or {}
            self.config_data.setdefault("sensors", []).append(editor.result)
            self.refresh_sensor_list()

    def edit_sensor(self):
        if not self.config_data:
            messagebox.showwarning("No config", "Load a configuration before editing.")
            return
        selection = self.sensor_list.curselection()
        if not selection:
            messagebox.showwarning("Select sensor", "Choose a sensor to edit.")
            return
        index = selection[0]
        sensor = self.config_data["sensors"][index]
        editor = config_ui.SensorEditor(self.root, "Edit sensor", sensor=sensor)
        if editor.result:
            self.config_data["sensors"][index] = editor.result
            self.refresh_sensor_list()

    def remove_sensor(self):
        if not self.config_data:
            messagebox.showwarning("No config", "Load a configuration before editing.")
            return
        selection = self.sensor_list.curselection()
        if not selection:
            messagebox.showwarning("Select sensor", "Choose a sensor to remove.")
            return
        index = selection[0]
        del self.config_data["sensors"][index]
        self.refresh_sensor_list()

    def attempt_serial_connection(self):
        port_target = 'COM12'
        self.status_label.config(text=f"Connecting to {port_target}...", foreground="orange")
        self.handshake_done = False
        self.kill_listen_thread = False
        self.root.update_idletasks()
        
        try:
            self.ser = serial.Serial(
                port=port_target, baudrate=115200,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=0.01  
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.serial_connected = True
            self.status_label.config(text=f"CONNECTED to {port_target}! Live Streams Enabled.", foreground="green")
            
            self.listen_thread = threading.Thread(target=self.serial_listen_loop, daemon=True)
            self.listen_thread.start()
                
        except Exception as e:
            self.serial_connected = False
            self.status_label.config(text=f"❌ UART LINK ERROR: {str(e).split('\n')[0]}", foreground="red")

    def disconnect_serial(self):
        self.kill_listen_thread = True
        self.serial_connected = False
        self.handshake_done = False
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.status_label.config(text="DISCONNECTED: Port closed. System idle.", foreground="red")

    def restart_and_sync(self):
        self.disconnect_serial()
        if self.listen_thread and self.listen_thread.is_alive():
            self.listen_thread.join(timeout=0.5) 
        
        self.device_registry.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        self.log_text.insert(tk.END, "\n--- SYSTEM RESET: Commencing Handshake Sync ---\n")
        self.raw_text.insert(tk.END, "\n--- CONSOLE REBOOT ---\n")
        self.log_text.see(tk.END)
        self.raw_text.see(tk.END)
        self.attempt_serial_connection()

    def update_ui_table(self, dev_id, name, format_str, status):
        for item in self.tree.get_children():
            if self.tree.item(item)['values'][0] == dev_id:
                self.tree.delete(item)
        self.tree.insert("", "end", values=(dev_id, name, format_str, status))

    def serial_listen_loop(self):
        raw_data = bytearray()
        HANDSHAKE_SYNCH = 0x55
        TELEMETRY_SYNCH = 0xAA
        last_poll_time = 0

        while self.serial_connected and not self.kill_listen_thread:
            try:
                if not self.handshake_done and (time.time() - last_poll_time > 0.2):
                    if self.ser and self.ser.is_open:
                        self.ser.write(b"\xAA")
                        last_poll_time = time.time()

                waiting = self.ser.in_waiting if (self.ser and self.ser.is_open) else 0
                if waiting > 0:
                    incoming = self.ser.read(waiting)
                    raw_data.extend(incoming)
                    # IMMEDIATELY push the un-parsed, raw wire dump to the right-hand panel
                    self.root.after(0, self.log_raw_bytes, incoming)
                else:
                    time.sleep(0.005)
                    continue

                while len(raw_data) > 0 and not self.kill_listen_thread:
                    h_idx = raw_data.index(HANDSHAKE_SYNCH) if HANDSHAKE_SYNCH in raw_data else 999999
                    t_idx = raw_data.index(TELEMETRY_SYNCH) if TELEMETRY_SYNCH in raw_data else 999999

                    if len(raw_data) >= 2 and raw_data[0:2] == b"\xAA\x55":
                        del raw_data[:2]
                        continue

                    if h_idx == 999999 and t_idx == 999999:
                        raw_data.clear()
                        break

                    # --- Process Handshake Registration Packet (0x55) ---
                    if h_idx < t_idx:
                        sync_idx = h_idx
                        if b"\r\n" not in raw_data[sync_idx:]: 
                            break  
                            
                        end_idx = raw_data.index(b"\r\n", sync_idx)
                        packet = raw_data[sync_idx:end_idx]
                        
                        if len(packet) < 4:
                            del raw_data[:sync_idx + 1]
                            continue

                        dev_id = packet[1]
                        payload_bytes = packet[2:-2]
                        raw_string = payload_bytes.decode('ascii', errors='ignore').strip()
                        received_crc, = struct.unpack("<H", packet[-2:])

                        crc_data_block = bytes([HANDSHAKE_SYNCH, dev_id]) + payload_bytes
                        if calculate_crc16(crc_data_block) == received_crc:
                            dev_name = raw_string
                            fmt_str = "None"
                            
                            if ":" in raw_string:
                                parts = raw_string.split(":", 1)
                                dev_name = parts[0]
                                raw_fmt = parts[1]
                                fmt_str = raw_fmt[1:] if raw_fmt and raw_fmt[0] in ('@', '=', '<', '>', '!') else raw_fmt
                            
                            self.device_registry[dev_id] = {"name": dev_name, "format": fmt_str}
                            self.ser.write(struct.pack("<H", received_crc))
                            self.handshake_done = True  
                            
                            self.root.after(0, self.update_ui_table, dev_id, dev_name, fmt_str, "Linked & Verified (ACK Success)")
                        else:
                            self.root.after(0, self.update_ui_table, dev_id, "Unknown", "Unknown", "PC Checksum Error")
                            
                        del raw_data[:end_idx + 2]

                    # --- Process Live Telemetry Packet (0xAA) ---
                    else:
                        sync_idx = t_idx
                        if len(raw_data) - sync_idx < 4: 
                            break

                        info_type = raw_data[sync_idx + 1] 
                        msg_id = raw_data[sync_idx + 2]    
                        msg_len = raw_data[sync_idx + 3]   
                        total_size = 4 + msg_len + 2 

                        if len(raw_data) - sync_idx < total_size: 
                            break

                        p_start = sync_idx + 4
                        p_end = p_start + msg_len
                        payload = bytes(raw_data[p_start:p_end])
                        received_crc, = struct.unpack("<H", raw_data[p_end:p_end+2])

                        crc_block = bytes([msg_id, msg_len, info_type]) + payload
                        
                        if calculate_crc16(crc_block) == received_crc:
                            dev_profile = self.device_registry.get(msg_id, {"name": f"Device ID: {msg_id}", "format": "None"})
                            dev_name = dev_profile["name"]
                            timestamp = time.strftime("%H:%M:%S")
                            parsed_output = ""
                            
                            if info_type == 0x10: 
                                try:
                                    raw_fmt = dev_profile["format"]
                                    if raw_fmt != "None":
                                        mode = self.alignment_var.get()
                                        fmt_string = "@" + raw_fmt if mode == "32-bit ARM (Cortex-M)" else "<" + raw_fmt
                                        python_fmt = fmt_string.replace('S', 's')
                                            
                                        expected_size = struct.calcsize(python_fmt)
                                        payload_len = len(payload)
                                        
                                        if payload_len < expected_size:
                                            payload_to_unpack = payload + b'\x00' * (expected_size - payload_len)
                                        else:
                                            payload_to_unpack = payload[:expected_size]
                                            
                                        unpacked_data = struct.unpack(python_fmt, payload_to_unpack)
                                        val_strings = []
                                        fmt_chars = [c for c in raw_fmt if c.isalpha() or c == 's' or c == 'S']
                                        for fmt, v in zip(fmt_chars, unpacked_data):
                                            if fmt == 'S':
                                                text = v.decode('ascii', errors='ignore').rstrip('\x00')
                                                val_strings.append(f'"{text}"')
                                            elif fmt == 's':
                                                val_strings.append("[" + ", ".join(f"0x{b:02X}" for b in v) + "]")
                                            elif isinstance(v, float):
                                                val_strings.append(f"{v:.3f}")
                                            else:
                                                val_strings.append(str(v))
                                        parsed_output = f"Struct Data: ({', '.join(val_strings)})"
                                    else:
                                        parsed_output = f"Unregistered Struct Data Hex: {payload.hex().upper()}"
                                except Exception as err:
                                    parsed_output = f"Alignment Unpack Error: {str(err)}"
                                    
                            elif info_type == 0x01: 
                                if len(payload) == 4:
                                    val, = struct.unpack("<f", payload)
                                    parsed_output = f"Float: {val:.3f}"
                            elif info_type == 0x05: 
                                parsed_output = f"Text: {payload.decode('ascii', errors='ignore').strip('\x00')}"
                            elif info_type == 0x03: 
                                if len(payload) == 4:
                                    val, = struct.unpack("<i", payload)
                                    parsed_output = f"Int32: {val}"
                            elif info_type == 0x06: 
                                if len(payload) == 2:
                                    val, = struct.unpack("<h", payload)
                                    parsed_output = f"Int16: {val}"
                            elif info_type == 0x04: 
                                if len(payload) == 1:
                                    val, = struct.unpack("<b", payload)
                                    parsed_output = f"Int8: {val}"
                            elif info_type == 0x02: 
                                parsed_output = f"Byte Array: [{', '.join(f'0x{b:02X}' for b in payload)}]"
                            else: 
                                parsed_output = f"Raw Hex [Type 0x{info_type:02X}]: {payload.hex().upper()}"

                            self.root.after(0, self.log_message, f"[{timestamp}] Node '{dev_name}' -> {parsed_output}")
                            # Add this:
                            for callback in self.subscribers:
                                callback(dev_name, parsed_output)
                        
                        del raw_data[:sync_idx + total_size]

            except Exception:
                self.serial_connected = False
                break

    def toggle_logging(self):
        if not self.is_logging:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            self.clean_file = open(f"clean_log_{timestamp}.txt", "w", encoding="utf-8")
            self.raw_file = open(f"raw_log_{timestamp}.txt", "w", encoding="utf-8")
            self.is_logging = True
            self.update_status("Logging started...", "blue")
        else:
            self.is_logging = False
            if self.clean_file: self.clean_file.close()
            if self.raw_file: self.raw_file.close()
            self.update_status("Logging stopped.", "green")

    def write_to_log(self, file_handle, data):
        if self.is_logging and file_handle:
            file_handle.write(data + "\n")
            file_handle.flush()
if __name__ == "__main__":
    root = tk.Tk()
    app = TelemetryApp(root)
    root.mainloop()
