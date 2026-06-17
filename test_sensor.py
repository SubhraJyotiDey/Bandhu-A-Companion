import os
import time
import sys
from daemon import ConfigManager
from hardware.person_sensor import PersonSensor

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.json")
    config_manager = ConfigManager(config_path)
    
    print("=================================================================")
    print("          PERSON SENSOR DIAGNOSTIC TOOL                          ")
    print("=================================================================")
    print(f"Config File Path: {config_path}")
    print(f"Mock Mode in config.json: {config_manager.config.get('mock', True)}")
    print(f"Face Tracking Config: {config_manager.config.get('face_tracking', {})}")
    print(f"Platform: {sys.platform}")
    
    # Force mock = False to test the physical I2C sensor
    print("\n[Diagnostic] Overriding mock mode to False to test physical hardware...")
    config_manager.config["mock"] = False
    
    # Initialize sensor
    sensor = PersonSensor(config_manager)
    
    if sensor.mock:
        print("\n[Diagnostic] ERROR: Sensor initialized in MOCK mode!")
        print("Reasons for mock mode fallback:")
        print(" 1. You are running on Windows/macOS where physical I2C does not exist.")
        print(" 2. The 'fcntl' library is not available.")
        print(" 3. The I2C bus (/dev/i2c-{}) or address (0x{:02x}) could not be opened.".format(
            config_manager.config.get("face_tracking", {}).get("i2c_bus", 1),
            config_manager.config.get("face_tracking", {}).get("sensor_address", 0x62)
        ))
        print("\nSuggestions:")
        print(" - Verify you are running this directly on the Raspberry Pi.")
        print(" - Make sure I2C is enabled on the Pi (run: sudo raspi-config -> Interface Options -> I2C).")
        print(" - Check your wiring: VCC to 3.3V, GND to GND, SDA to SDA (GPIO 2), SCL to SCL (GPIO 3).")
        print(" - Run: ls /dev/i2c* to see if the I2C bus is visible.")
        print(" - Run: sudo i2cdetect -y 1 to verify if address 0x62 is detected on the bus.")
        sys.exit(1)
        
    print("[Diagnostic] Sensor initialized successfully on I2C.")
    print("[Diagnostic] Starting background read loop...")
    sensor.start()
    
    try:
        print("\nReading sensor data... (Press Ctrl+C to exit)\n")
        while True:
            faces_count = len(sensor.faces)
            primary = sensor.get_primary_face()
            
            if faces_count > 0:
                print(f"\r[DETECTED] Faces: {faces_count} | Primary Center: ({primary['x']:.1f}, {primary['y']:.1f}) | Conf: {primary['confidence']}% | Facing: {primary['is_facing']}", end="", flush=True)
            else:
                print("\r[SCANNING] No faces detected...                                                         ", end="", flush=True)
                
            time.sleep(0.2)
            
    except KeyboardInterrupt:
        print("\n\n[Diagnostic] Exiting on user request.")
    finally:
        sensor.stop()
        print("[Diagnostic] Sensor stopped cleanly.")

if __name__ == "__main__":
    main()
