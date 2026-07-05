import tkinter as tk
from tkinter import ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import re

class PlotWindow:
    def __init__(self, parent, app_instance):
        self.app = app_instance
        self.window = tk.Toplevel(parent)
        self.window.title("Live Sensor Data Plotter")
        self.window.geometry("800x700")
        
        self.app.subscribers.append(self.on_data_received)
        self.data_buffer = []
        self.max_samples = 50 # Default sample count
        
        # UI: Dropdowns
        tk.Label(self.window, text="Select Sensor:").pack(pady=(5, 0))
        self.sensor_var = tk.StringVar()
        sensors = [s["name"] for s in self.app.config_data.get("sensors", [])]
        self.sensor_dropdown = ttk.Combobox(self.window, textvariable=self.sensor_var, values=sensors)
        self.sensor_dropdown.pack(pady=5, fill="x", padx=20)
        self.sensor_dropdown.bind("<<ComboboxSelected>>", self.on_sensor_select)
        
        tk.Label(self.window, text="Select Field:").pack(pady=(5, 0))
        self.field_var = tk.StringVar()
        self.field_dropdown = ttk.Combobox(self.window, textvariable=self.field_var)
        self.field_dropdown.pack(pady=5, fill="x", padx=20)

        # FIX: Sample size selection dropdown
        tk.Label(self.window, text="Max Samples to Plot:").pack(pady=(5, 0))
        self.samples_var = tk.StringVar(value="50")
        self.samples_dropdown = ttk.Combobox(self.window, textvariable=self.samples_var, values=["20", "50", "100", "200", "500"])
        self.samples_dropdown.pack(pady=5, fill="x", padx=20)
        self.samples_dropdown.bind("<<ComboboxSelected>>", self.on_samples_change)

        # Plot Setup
        self.fig = Figure(figsize=(7, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.window)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def on_samples_change(self, event):
        # Update limit and clear buffer to prevent scaling glitches
        self.max_samples = int(self.samples_var.get())
        self.data_buffer = self.data_buffer[-self.max_samples:]

    def on_sensor_select(self, event):
        sensor_name = self.sensor_var.get()
        sensor = next((s for s in self.app.config_data.get("sensors", []) if s["name"] == sensor_name), None)
        self.field_dropdown["values"] = [f["name"] for f in sensor.get("struct_fields", [])] if sensor else ["Value"]
        self.field_dropdown.current(0)
        self.data_buffer = [] # Clear data on switch

    def on_data_received(self, dev_name, parsed_data):
        if dev_name == self.sensor_var.get():
            numbers = re.findall(r"[-+]?\d*\.\d+|\d+", parsed_data)
            idx = self.field_dropdown.current()
            if idx == -1: idx = 0
            
            if len(numbers) > idx:
                val = float(numbers[idx])
                self.data_buffer.append(val)
                
                # Maintain buffer size
                if len(self.data_buffer) > self.max_samples:
                    self.data_buffer.pop(0)
                
                self.ax.clear()
                self.ax.plot(self.data_buffer)
                self.ax.set_title(f"Live: {dev_name} - {self.field_var.get()}")
                self.ax.set_ylim(auto=True) # Forces re-scaling based on current buffer only
                self.canvas.draw()
