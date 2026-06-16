import sys

class GPIOManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.config = config_manager.config
        self.mock = self.config.get("mock", True)
        
        # If on Windows, force mock mode
        if sys.platform.startswith("win"):
            self.mock = True
            
        # Initialize pins tracking
        self.pins_state = {}
        self.gpio_devices = {}
        
        self._load_pins_from_config()
        
        if not self.mock:
            self._init_gpio()

    def _load_pins_from_config(self):
        gpios_list = self.config.get("gpios", [])
        for gpio in gpios_list:
            pin = gpio.get("pin")
            name = gpio.get("name", f"GPIO {pin}")
            state = gpio.get("state", False)
            self.pins_state[pin] = {
                "name": name,
                "state": state
            }

    def _init_gpio(self):
        print("[GPIO] Initializing physical GPIO pins...")
        try:
            from gpiozero import OutputDevice
            for pin, info in self.pins_state.items():
                try:
                    # Initialize OutputDevice, setting its initial value based on config
                    self.gpio_devices[pin] = OutputDevice(pin, active_high=True, initial_value=info["state"])
                    print(f"[GPIO] Initialized physical pin {pin} ({info['name']}) to state: {info['state']}")
                except Exception as ex:
                    print(f"[GPIO Error] Failed to initialize pin {pin}: {ex}")
        except ImportError:
            print("[GPIO Error] gpiozero library not found. Switching to Mock Mode.")
            self.mock = True

    def set_pin_state(self, pin_num, state):
        """Sets the state of a GPIO pin (True/ON or False/OFF)."""
        pin_num = int(pin_num)
        state = bool(state)
        
        if pin_num not in self.pins_state:
            # Dynamically register pin if not in config
            self.pins_state[pin_num] = {
                "name": f"Dynamic GPIO {pin_num}",
                "state": state
            }
            # Save it back to config
            if "gpios" not in self.config:
                self.config["gpios"] = []
            self.config["gpios"].append({
                "pin": pin_num,
                "name": f"Dynamic GPIO {pin_num}",
                "state": state
            })
            self.config_manager.save_config()
            
        # Update state memory
        self.pins_state[pin_num]["state"] = state
        
        # Sync with config list
        for gpio in self.config.get("gpios", []):
            if gpio.get("pin") == pin_num:
                gpio["state"] = state
                self.config_manager.save_config()
                break
                
        if self.mock:
            print(f"[GPIO MOCK] Pin {pin_num} ({self.pins_state[pin_num]['name']}) toggled to: {'ON' if state else 'OFF'}")
            return True
            
        # Physical control
        try:
            from gpiozero import OutputDevice
            if pin_num not in self.gpio_devices:
                self.gpio_devices[pin_num] = OutputDevice(pin_num, active_high=True, initial_value=state)
                
            device = self.gpio_devices[pin_num]
            if state:
                device.on()
            else:
                device.off()
            print(f"[GPIO] Physical Pin {pin_num} set to: {'ON' if state else 'OFF'}")
            return True
        except Exception as e:
            print(f"[GPIO Error] Failed to write to pin {pin_num}: {e}")
            return False

    def get_pins_status(self):
        """Returns pin statuses for reporting to the portal."""
        # Refresh mock states
        self.mock = self.config_manager.config.get("mock", True)
        if sys.platform.startswith("win"):
            self.mock = True
            
        return [
            {
                "pin": pin,
                "name": info["name"],
                "state": info["state"]
            }
            for pin, info in self.pins_state.items()
        ]
