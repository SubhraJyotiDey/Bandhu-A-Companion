import sys
import time
import argparse

# Subsystem imports
from daemon import CompanionDaemon
from mcp.server import run_mcp_server

def handle_direct_execute():
    """Handles direct command-line tool calls (e.g. from ZeroClaw native skill)."""
    if len(sys.argv) >= 3 and sys.argv[1] == "execute":
        tool_name = sys.argv[2]
        arguments = {}
        for arg in sys.argv[3:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                # Automatically convert integers
                if v.isdigit():
                    v = int(v)
                elif v.lower() == "true":
                    v = True
                elif v.lower() == "false":
                    v = False
                arguments[k] = v
                
        # Send query to local Flask daemon
        import urllib.request
        import json
        url = "http://127.0.0.1:5000/api/mcp/execute"
        payload = json.dumps({"tool": tool_name, "arguments": arguments}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as res:
                res_body = res.read().decode("utf-8")
                res_json = json.loads(res_body)
                if res_json.get("success", False):
                    print(res_json.get("message", "Success"))
                else:
                    print(f"Error: {res_json.get('error', 'Execution failed')}")
        except Exception as e:
            print(f"Error: Failed to connect to companion daemon (is it running?): {e}")
        sys.exit(0)

def main():
    handle_direct_execute()
    parser = argparse.ArgumentParser(description="ZeroClaw Mechanical Eye & Voice Companion Daemon")
    parser.add_argument(
        "action", 
        nargs="?", 
        default="run", 
        choices=["run", "mcp", "test", "execute"],
        help="Action to perform: 'run' starts the daemon/web portal (default), 'mcp' launches the stdio server for ZeroClaw, 'test' runs quick hardware diagnostics, 'execute' runs direct commands."
    )
    
    args = parser.parse_args()
    
    if args.action == "run":
        # Launching full daemon service
        print("=================================================================")
        print("          CLAW-EYE COMPANION DAEMON STARTING                     ")
        print("=================================================================")
        
        daemon = CompanionDaemon()
        daemon.start()
        
        # Start the Flask web portal (blocks in thread, daemon handles background)
        # Import dynamically to prevent import issues in thin MCP mode
        from web.app import run_web_portal
        
        # We start the web portal on port 5000. 
        # This will block and serve requests, keeping the process alive
        try:
            run_web_portal(daemon, host="0.0.0.0", port=5000)
            
            # Main thread keeps running and logs updates
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[Main] Terminating companion daemon...")
            daemon.stop()
            print("[Main] Companion daemon shut down cleanly.")
            
    elif args.action == "mcp":
        # Launch stdio MCP server for ZeroClaw
        try:
            run_mcp_server()
        except KeyboardInterrupt:
            pass
            
    elif args.action == "test":
        # Diagnostic test: print state
        print("[Diagnostic] Running quick checks...")
        daemon = CompanionDaemon()
        print(f"[Diagnostic] Platform: {sys.platform}")
        print(f"[Diagnostic] Mock Mode Active: {daemon.servos.mock}")
        print(f"[Diagnostic] Servo Mode Configured: {daemon.servos.servo_mode}")
        print(f"[Diagnostic] Exiting diagnostic.")

if __name__ == "__main__":
    main()
